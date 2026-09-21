// NX MCP GUI bridge: an NX Open .NET add-in that serves NX MCP bridge protocol v1
// from inside an interactive NX session without blocking the NX user interface.
//
// A background thread owns the authenticated loopback socket. Every NXOpen call is
// marshaled onto NX's UI thread through a hidden WinForms control and runs only
// while NX grants UF_UI_lock_ug_access, i.e. between user commands. The JSON-RPC
// envelope, error codes and command results match src/nx_mcp/nx_bridge.py, so the
// Python MCP sidecar works unchanged. See docs/gui-bridge.md.
//
// Written for the in-box .NET Framework compiler (C# 5); build with build.ps1.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using NXOpen;
using NXOpen.Features;
using NXOpen.GeometricUtilities;
using NXOpen.UF;
using Thread = System.Threading.Thread;

/// <summary>NX entry points: Startup (startup folder), Main (File > Execute > NX Open).</summary>
public static class NxMcpGuiBridge
{
    public static int Startup()
    {
        NxMcp.GuiBridge.BridgeHost.StartFromStartup();
        return 0;
    }

    public static void Main(string[] args)
    {
        NxMcp.GuiBridge.BridgeHost.Toggle();
    }

    public static int GetUnloadOption(string arg)
    {
        return (int)Session.LibraryUnloadOption.AtTermination;
    }
}

namespace NxMcp.GuiBridge
{
    internal static class Protocol
    {
        public const int Version = 1;
        public const int MaxMessageBytes = 1024 * 1024;
        public static readonly TimeSpan ReadTimeout = TimeSpan.FromSeconds(5);
        public static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
    }

    internal sealed class NxToolError : Exception
    {
        public readonly string Code;
        public string Suggestion;
        public readonly object NxCode;
        public readonly bool Retryable;
        public readonly Dictionary<string, object> Details;

        public NxToolError(string code, string message)
            : this(code, message, null, null, false, null)
        {
        }

        public NxToolError(
            string code,
            string message,
            string suggestion,
            object nxCode,
            bool retryable,
            Dictionary<string, object> details)
            : base(message)
        {
            Code = code;
            Suggestion = suggestion;
            NxCode = nxCode;
            Retryable = retryable;
            Details = details ?? new Dictionary<string, object>();
        }

        public Dictionary<string, object> ToDictionary()
        {
            var result = new Dictionary<string, object>();
            result["status"] = "error";
            result["code"] = Code;
            result["message"] = Message;
            result["retryable"] = Retryable;
            if (!string.IsNullOrEmpty(Suggestion))
            {
                result["suggestion"] = Suggestion;
            }
            if (NxCode != null)
            {
                result["nx_code"] = NxCode;
            }
            if (Details.Count > 0)
            {
                result["details"] = Details;
            }
            return result;
        }

        public static Dictionary<string, object> State(string executionState)
        {
            var details = new Dictionary<string, object>();
            details["execution_state"] = executionState;
            return details;
        }
    }

    internal sealed class WorkspaceViolation : Exception
    {
        public WorkspaceViolation()
            : base("Path must stay inside the configured workspace")
        {
        }
    }

    /// <summary>Strict, culture-invariant JSON for the bridge envelope.</summary>
    internal static class Json
    {
        private const int MaxDepth = 100;

        public static object Parse(string text)
        {
            var parser = new Parser(text);
            parser.SkipWhitespace();
            object value = parser.ParseValue(0);
            parser.SkipWhitespace();
            if (!parser.AtEnd)
            {
                throw new FormatException("Unexpected trailing JSON content");
            }
            return value;
        }

        public static string Serialize(object value)
        {
            var builder = new StringBuilder();
            Write(builder, value, 0);
            return builder.ToString();
        }

        private static void Write(StringBuilder builder, object value, int depth)
        {
            if (depth > MaxDepth)
            {
                throw new ArgumentException("JSON value is nested too deeply");
            }
            if (value == null)
            {
                builder.Append("null");
            }
            else if (value is string)
            {
                WriteString(builder, (string)value);
            }
            else if (value is bool)
            {
                builder.Append((bool)value ? "true" : "false");
            }
            else if (value is int || value is long || value is uint || value is ulong || value is short)
            {
                builder.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
            }
            else if (value is double || value is float || value is decimal)
            {
                double number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                if (double.IsNaN(number) || double.IsInfinity(number))
                {
                    throw new ArgumentException("Non-finite JSON numbers are not allowed");
                }
                builder.Append(number.ToString("R", CultureInfo.InvariantCulture));
            }
            else if (value is IDictionary)
            {
                builder.Append('{');
                bool first = true;
                foreach (DictionaryEntry entry in (IDictionary)value)
                {
                    if (!first)
                    {
                        builder.Append(',');
                    }
                    first = false;
                    WriteString(builder, Convert.ToString(entry.Key, CultureInfo.InvariantCulture));
                    builder.Append(':');
                    Write(builder, entry.Value, depth + 1);
                }
                builder.Append('}');
            }
            else if (value is IEnumerable)
            {
                builder.Append('[');
                bool first = true;
                foreach (object item in (IEnumerable)value)
                {
                    if (!first)
                    {
                        builder.Append(',');
                    }
                    first = false;
                    Write(builder, item, depth + 1);
                }
                builder.Append(']');
            }
            else
            {
                throw new ArgumentException("Unsupported JSON value type: " + value.GetType().Name);
            }
        }

        private static void WriteString(StringBuilder builder, string text)
        {
            builder.Append('"');
            foreach (char character in text)
            {
                switch (character)
                {
                    case '"':
                        builder.Append("\\\"");
                        break;
                    case '\\':
                        builder.Append("\\\\");
                        break;
                    case '\n':
                        builder.Append("\\n");
                        break;
                    case '\r':
                        builder.Append("\\r");
                        break;
                    case '\t':
                        builder.Append("\\t");
                        break;
                    default:
                        if (character < 0x20)
                        {
                            builder.Append("\\u").Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            builder.Append(character);
                        }
                        break;
                }
            }
            builder.Append('"');
        }

        private sealed class Parser
        {
            private readonly string text;
            private int index;

            public Parser(string text)
            {
                this.text = text;
            }

            public bool AtEnd
            {
                get { return index >= text.Length; }
            }

            public void SkipWhitespace()
            {
                while (!AtEnd && (text[index] == ' ' || text[index] == '\t' || text[index] == '\n' || text[index] == '\r'))
                {
                    index++;
                }
            }

            public object ParseValue(int depth)
            {
                if (depth > MaxDepth)
                {
                    throw new FormatException("JSON value is nested too deeply");
                }
                if (AtEnd)
                {
                    throw new FormatException("Unexpected end of JSON");
                }
                char character = text[index];
                if (character == '{')
                {
                    return ParseObject(depth + 1);
                }
                if (character == '[')
                {
                    return ParseArray(depth + 1);
                }
                if (character == '"')
                {
                    return ParseString();
                }
                if (character == '-' || (character >= '0' && character <= '9'))
                {
                    return ParseNumber();
                }
                if (Consume("true"))
                {
                    return true;
                }
                if (Consume("false"))
                {
                    return false;
                }
                if (Consume("null"))
                {
                    return null;
                }
                throw new FormatException("Invalid JSON value");
            }

            private bool Consume(string literal)
            {
                if (string.CompareOrdinal(text, index, literal, 0, literal.Length) != 0)
                {
                    return false;
                }
                index += literal.Length;
                return true;
            }

            private void Expect(char character)
            {
                if (AtEnd || text[index] != character)
                {
                    throw new FormatException("Expected '" + character + "' in JSON");
                }
                index++;
            }

            private Dictionary<string, object> ParseObject(int depth)
            {
                Expect('{');
                var result = new Dictionary<string, object>(StringComparer.Ordinal);
                SkipWhitespace();
                if (!AtEnd && text[index] == '}')
                {
                    index++;
                    return result;
                }
                while (true)
                {
                    SkipWhitespace();
                    if (AtEnd || text[index] != '"')
                    {
                        throw new FormatException("Expected a JSON object key");
                    }
                    string key = ParseString();
                    SkipWhitespace();
                    Expect(':');
                    SkipWhitespace();
                    result[key] = ParseValue(depth);
                    SkipWhitespace();
                    if (!AtEnd && text[index] == ',')
                    {
                        index++;
                        continue;
                    }
                    Expect('}');
                    return result;
                }
            }

            private List<object> ParseArray(int depth)
            {
                Expect('[');
                var result = new List<object>();
                SkipWhitespace();
                if (!AtEnd && text[index] == ']')
                {
                    index++;
                    return result;
                }
                while (true)
                {
                    SkipWhitespace();
                    result.Add(ParseValue(depth));
                    SkipWhitespace();
                    if (!AtEnd && text[index] == ',')
                    {
                        index++;
                        continue;
                    }
                    Expect(']');
                    return result;
                }
            }

            private string ParseString()
            {
                Expect('"');
                var builder = new StringBuilder();
                while (true)
                {
                    if (AtEnd)
                    {
                        throw new FormatException("Unterminated JSON string");
                    }
                    char character = text[index++];
                    if (character == '"')
                    {
                        return builder.ToString();
                    }
                    if (character < 0x20)
                    {
                        throw new FormatException("Control character in JSON string");
                    }
                    if (character != '\\')
                    {
                        builder.Append(character);
                        continue;
                    }
                    if (AtEnd)
                    {
                        throw new FormatException("Unterminated JSON escape");
                    }
                    char escape = text[index++];
                    switch (escape)
                    {
                        case '"':
                            builder.Append('"');
                            break;
                        case '\\':
                            builder.Append('\\');
                            break;
                        case '/':
                            builder.Append('/');
                            break;
                        case 'b':
                            builder.Append('\b');
                            break;
                        case 'f':
                            builder.Append('\f');
                            break;
                        case 'n':
                            builder.Append('\n');
                            break;
                        case 'r':
                            builder.Append('\r');
                            break;
                        case 't':
                            builder.Append('\t');
                            break;
                        case 'u':
                            if (index + 4 > text.Length)
                            {
                                throw new FormatException("Invalid JSON unicode escape");
                            }
                            builder.Append((char)int.Parse(
                                text.Substring(index, 4), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture));
                            index += 4;
                            break;
                        default:
                            throw new FormatException("Invalid JSON escape");
                    }
                }
            }

            private object ParseNumber()
            {
                int start = index;
                if (text[index] == '-')
                {
                    index++;
                }
                if (AtEnd)
                {
                    throw new FormatException("Invalid JSON number");
                }
                if (text[index] == '0')
                {
                    index++;
                }
                else if (text[index] >= '1' && text[index] <= '9')
                {
                    SkipDigits();
                }
                else
                {
                    throw new FormatException("Invalid JSON number");
                }
                bool integral = true;
                if (!AtEnd && text[index] == '.')
                {
                    integral = false;
                    index++;
                    RequireDigits();
                }
                if (!AtEnd && (text[index] == 'e' || text[index] == 'E'))
                {
                    integral = false;
                    index++;
                    if (!AtEnd && (text[index] == '+' || text[index] == '-'))
                    {
                        index++;
                    }
                    RequireDigits();
                }
                string token = text.Substring(start, index - start);
                long whole;
                if (integral && long.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole))
                {
                    return whole;
                }
                double number = double.Parse(token, NumberStyles.Float, CultureInfo.InvariantCulture);
                if (double.IsNaN(number) || double.IsInfinity(number))
                {
                    throw new FormatException("Non-finite JSON numbers are not allowed");
                }
                return number;
            }

            private void RequireDigits()
            {
                if (AtEnd || text[index] < '0' || text[index] > '9')
                {
                    throw new FormatException("Invalid JSON number");
                }
                SkipDigits();
            }

            private void SkipDigits()
            {
                while (!AtEnd && text[index] >= '0' && text[index] <= '9')
                {
                    index++;
                }
            }
        }
    }

    internal sealed class Workspace
    {
        public readonly string Root;

        public Workspace(string root)
        {
            Root = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        /// <summary>Normalize a path and reject anything outside the root or behind a link.</summary>
        public string EnsureInside(string path)
        {
            if (string.IsNullOrEmpty(path) || path.IndexOf('\0') >= 0)
            {
                throw new WorkspaceViolation();
            }
            string full;
            try
            {
                full = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            }
            catch (Exception)
            {
                throw new WorkspaceViolation();
            }
            bool inside = string.Equals(full, Root, StringComparison.OrdinalIgnoreCase) ||
                full.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
            if (!inside)
            {
                throw new WorkspaceViolation();
            }
            // Path.GetFullPath does not follow junctions or symlinks, so refuse to cross one.
            string current = Root;
            foreach (string part in full.Substring(Root.Length).Split(
                new[] { Path.DirectorySeparatorChar }, StringSplitOptions.RemoveEmptyEntries))
            {
                current = Path.Combine(current, part);
                if ((File.Exists(current) || Directory.Exists(current)) &&
                    (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new WorkspaceViolation();
                }
            }
            return full;
        }
    }

    internal sealed class ObjectRef
    {
        public string Id;
        public string Kind;
        public string Name;
        public string PartId;

        public Dictionary<string, object> ToDictionary()
        {
            var result = new Dictionary<string, object>();
            result["id"] = Id;
            result["kind"] = Kind;
            result["name"] = Name;
            result["part_id"] = PartId;
            return result;
        }
    }

    /// <summary>Opaque, session-scoped IDs for live NXOpen objects (mirrors ObjectRegistry).</summary>
    internal sealed class ObjectRegistry
    {
        private sealed class Entry
        {
            public object Value;
            public ObjectRef Reference;
        }

        private readonly Dictionary<string, Entry> objects = new Dictionary<string, Entry>();
        private readonly HashSet<string> staleIds = new HashSet<string>();
        private readonly Dictionary<string, string> identities = new Dictionary<string, string>();

        public ObjectRef Register(object value, string kind, string name, string partId)
        {
            string identityKey = partId + "|" + kind + "|" + Executor.Identity(value);
            string existing;
            if (identities.TryGetValue(identityKey, out existing))
            {
                return objects[existing].Reference;
            }
            var reference = new ObjectRef
            {
                Id = "obj_" + Guid.NewGuid().ToString("N"),
                Kind = kind,
                Name = name,
                PartId = partId,
            };
            objects[reference.Id] = new Entry { Value = value, Reference = reference };
            identities[identityKey] = reference.Id;
            return reference;
        }

        public object Resolve(string objectId, string expectedKind, string partId)
        {
            Entry entry;
            if (!objects.TryGetValue(objectId, out entry))
            {
                string code = staleIds.Contains(objectId) ? "NX_OBJECT_STALE" : "NX_OBJECT_NOT_FOUND";
                throw new NxToolError(code, "Object reference is not valid: " + objectId);
            }
            if (expectedKind != null && entry.Reference.Kind != expectedKind)
            {
                throw new NxToolError(
                    "NX_OBJECT_TYPE_MISMATCH", "Expected " + expectedKind + ", got " + entry.Reference.Kind);
            }
            if (partId != null && entry.Reference.PartId != partId)
            {
                throw new NxToolError("NX_OBJECT_STALE", "Object reference belongs to a different work part");
            }
            return entry.Value;
        }

        public void InvalidatePart(string partId)
        {
            var invalid = new List<string>();
            foreach (KeyValuePair<string, Entry> pair in objects)
            {
                if (pair.Value.Reference.PartId == partId)
                {
                    invalid.Add(pair.Key);
                }
            }
            foreach (string objectId in invalid)
            {
                objects.Remove(objectId);
                staleIds.Add(objectId);
            }
            var keep = new Dictionary<string, string>();
            foreach (KeyValuePair<string, string> pair in identities)
            {
                if (!pair.Key.StartsWith(partId + "|", StringComparison.Ordinal))
                {
                    keep[pair.Key] = pair.Value;
                }
            }
            identities.Clear();
            foreach (KeyValuePair<string, string> pair in keep)
            {
                identities[pair.Key] = pair.Value;
            }
        }
    }

    /// <summary>C# port of NXOpenExecutor: the certified commands against the live session.</summary>
    internal sealed class Executor
    {
        private delegate Dictionary<string, object> Handler(Dictionary<string, object> values);

        private sealed class Command
        {
            public string[] Required;
            public Dictionary<string, object> Optional;
            public Handler Run;
        }

        private static readonly HashSet<string> Mutations = new HashSet<string>
        {
            "nx_create_sketch", "nx_sketch_line", "nx_sketch_rectangle", "nx_finish_sketch", "nx_extrude",
        };

        private static readonly HashSet<string> Queries = new HashSet<string>
        {
            "nx_status", "nx_list_sketches", "nx_list_bodies", "nx_list_features",
        };

        private readonly Session session;
        private readonly string nxVersion;
        private readonly Workspace workspace;
        private readonly ObjectRegistry objects = new ObjectRegistry();
        private readonly List<Session.UndoMarkId> undoMarks = new List<Session.UndoMarkId>();
        private readonly HashSet<string> unsafeParts = new HashSet<string>();
        private readonly Dictionary<string, Command> commands = new Dictionary<string, Command>();
        private string undoPartId;

        public Executor(Session session, string nxVersion, Workspace workspace)
        {
            this.session = session;
            this.nxVersion = nxVersion;
            this.workspace = workspace;
            Add("nx_status", Status);
            Add("nx_create_part", CreatePart, "path", Opt("units", "mm"));
            Add("nx_open_part", OpenPart, "path");
            Add("nx_save_part", SavePart);
            Add("nx_close_part", ClosePart, Opt("save", true));
            Add("nx_export_step", ExportStep, "path");
            Add("nx_list_sketches", delegate { return ListObjects("sketch"); });
            Add("nx_list_bodies", delegate { return ListObjects("body"); });
            Add("nx_list_features", delegate { return ListObjects("feature"); });
            Add("nx_create_sketch", CreateSketch, Opt("plane", "XY"), Opt("name", null));
            Add("nx_sketch_line", SketchLine, "sketch_id", "start", "end");
            Add("nx_sketch_rectangle", SketchRectangle, "sketch_id", "corner1", "corner2");
            Add("nx_finish_sketch", FinishSketch, "sketch_id");
            Add("nx_extrude", Extrude, "sketch_id", "distance", Opt("reverse", false));
            Add("nx_undo", Undo);
            Add("nx_fit_view", FitView);
        }

        private static KeyValuePair<string, object> Opt(string name, object defaultValue)
        {
            return new KeyValuePair<string, object>(name, defaultValue);
        }

        private void Add(string method, Handler run, params object[] parameters)
        {
            var command = new Command
            {
                Run = run,
                Optional = new Dictionary<string, object>(),
            };
            var required = new List<string>();
            foreach (object parameter in parameters)
            {
                if (parameter is string)
                {
                    required.Add((string)parameter);
                }
                else
                {
                    var optional = (KeyValuePair<string, object>)parameter;
                    command.Optional[optional.Key] = optional.Value;
                }
            }
            command.Required = required.ToArray();
            commands[method] = command;
        }

        public Dictionary<string, object> Execute(string method, Dictionary<string, object> parameters)
        {
            Part part = WorkPart(false);
            string partId = part != null ? PartId(part) : null;
            SyncUndoPart(partId);
            object save;
            bool discardClose = method == "nx_close_part" &&
                parameters.TryGetValue("save", out save) && save is bool && !(bool)save;
            if (partId != null && unsafeParts.Contains(partId) && !Queries.Contains(method) && !discardClose)
            {
                throw new NxToolError(
                    "NX_ROLLBACK_FAILED",
                    "This part requires recovery: close without saving and reopen, or restart the bridge.");
            }
            Command command;
            if (!commands.TryGetValue(method, out command))
            {
                throw new NxToolError("NX_TOOL_NOT_FOUND", "Unsupported bridge command: " + method);
            }
            Dictionary<string, object> values = Bind(command, parameters);
            Validate(values);
            Session.UndoMarkId undoMark = default(Session.UndoMarkId);
            bool marked = false;
            try
            {
                if (Mutations.Contains(method))
                {
                    WorkPart(true);
                    undoMark = session.SetUndoMark(Session.MarkVisibility.Visible, "NX MCP: " + method);
                    marked = true;
                }
                Dictionary<string, object> result = command.Run(values);
                Part current = WorkPart(false);
                SyncUndoPart(current != null ? PartId(current) : null);
                if (marked)
                {
                    undoMarks.Add(undoMark);
                }
                return result;
            }
            catch (WorkspaceViolation error)
            {
                throw new NxToolError("NX_PATH_OUTSIDE_WORKSPACE", error.Message);
            }
            catch (NxToolError error)
            {
                if (marked)
                {
                    Rollback(undoMark, error, partId);
                }
                throw;
            }
            catch (Exception error)
            {
                if (marked)
                {
                    Rollback(undoMark, error, partId);
                }
                NXException native = error as NXException;
                throw new NxToolError(
                    "NX_API_ERROR", error.Message, null, native != null ? (object)native.ErrorCode : null, false, null);
            }
        }

        private static Dictionary<string, object> Bind(Command command, Dictionary<string, object> parameters)
        {
            foreach (string name in parameters.Keys)
            {
                if (Array.IndexOf(command.Required, name) < 0 && !command.Optional.ContainsKey(name))
                {
                    throw SignatureMismatch();
                }
            }
            var values = new Dictionary<string, object>();
            foreach (string name in command.Required)
            {
                if (!parameters.ContainsKey(name))
                {
                    throw SignatureMismatch();
                }
                values[name] = parameters[name];
            }
            foreach (KeyValuePair<string, object> optional in command.Optional)
            {
                object value;
                values[optional.Key] = parameters.TryGetValue(optional.Key, out value) ? value : optional.Value;
            }
            return values;
        }

        private static NxToolError SignatureMismatch()
        {
            return Invalid("Command parameters do not match its signature");
        }

        private static NxToolError Invalid(string message)
        {
            return new NxToolError("NX_INVALID_ARGUMENT", message);
        }

        private void Validate(Dictionary<string, object> values)
        {
            CheckChoice(values, "units", "mm", "inch");
            CheckChoice(values, "plane", "XY", "XZ", "YZ");
            foreach (string field in new[] { "path", "sketch_id" })
            {
                if (values.ContainsKey(field))
                {
                    string text = values[field] as string;
                    if (text == null || text.Trim().Length == 0 || text.IndexOf('\0') >= 0)
                    {
                        throw Invalid(field + " must be a non-empty string");
                    }
                }
            }
            if (values.ContainsKey("name") && values["name"] != null && !(values["name"] is string))
            {
                throw Invalid("name must be a string or null");
            }
            foreach (string field in new[] { "save", "reverse" })
            {
                if (values.ContainsKey(field) && !(values[field] is bool))
                {
                    throw Invalid(field + " must be a boolean");
                }
            }
            if (values.ContainsKey("distance") && Number(values["distance"]) <= 0)
            {
                throw Invalid("distance must be greater than zero");
            }
            var points = new Dictionary<string, double[]>();
            foreach (string field in new[] { "start", "end", "corner1", "corner2" })
            {
                if (values.ContainsKey(field))
                {
                    var point = values[field] as Dictionary<string, object>;
                    if (point == null || point.Count != 2 || !point.ContainsKey("x") || !point.ContainsKey("y"))
                    {
                        throw Invalid(field + " must contain x and y");
                    }
                    points[field] = new[] { Number(point["x"]), Number(point["y"]) };
                }
            }
            if (points.ContainsKey("start") && points["start"][0] == points["end"][0] &&
                points["start"][1] == points["end"][1])
            {
                throw Invalid("Line length must be non-zero");
            }
            if (points.ContainsKey("corner1") && (points["corner1"][0] == points["corner2"][0] ||
                points["corner1"][1] == points["corner2"][1]))
            {
                throw Invalid("Rectangle width and height must be non-zero");
            }
            if (values.ContainsKey("sketch_id"))
            {
                objects.Resolve((string)values["sketch_id"], "sketch", PartId(WorkPart(true)));
            }
        }

        private static void CheckChoice(Dictionary<string, object> values, string field, params string[] choices)
        {
            if (values.ContainsKey(field))
            {
                string text = values[field] as string;
                if (text == null || Array.IndexOf(choices, text) < 0)
                {
                    throw Invalid("Invalid " + field);
                }
            }
        }

        private static double Number(object value)
        {
            if (value is long || value is int || value is double)
            {
                double number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                if (!double.IsNaN(number) && !double.IsInfinity(number))
                {
                    return number;
                }
            }
            throw Invalid("Coordinates and distances must be finite numbers");
        }

        private static string FormatNumber(object value)
        {
            if (value is double)
            {
                return ((double)value).ToString("R", CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        private void Rollback(Session.UndoMarkId undoMark, Exception originalError, string partId)
        {
            try
            {
                session.UndoToMark(undoMark, null);
                session.DeleteUndoMark(undoMark, null);
            }
            catch (Exception rollbackError)
            {
                undoMarks.Clear();
                if (partId != null)
                {
                    unsafeParts.Add(partId);
                }
                var details = new Dictionary<string, object>();
                details["operation_error"] = originalError.Message;
                details["rollback_error"] = rollbackError.Message;
                throw new NxToolError(
                    "NX_ROLLBACK_FAILED",
                    "Operation failed and rollback requires recovery: close without saving and reopen, or restart the bridge.",
                    null,
                    null,
                    false,
                    details);
            }
            finally
            {
                if (partId != null)
                {
                    objects.InvalidatePart(partId);
                }
            }
        }

        private void SyncUndoPart(string partId)
        {
            if (undoPartId != partId)
            {
                undoMarks.Clear();
                undoPartId = partId;
            }
        }

        private Part WorkPart(bool required)
        {
            Part part = session.Parts.Work;
            if (part == null && required)
            {
                throw new NxToolError(
                    "NX_NO_WORK_PART", "No work part is open.", "Use nx_open_part or nx_create_part first.", null, false, null);
            }
            return part;
        }

        internal static string Identity(object value)
        {
            var native = value as TaggedObject;
            return native != null
                ? ((ulong)native.Tag).ToString(CultureInfo.InvariantCulture)
                : "clr" + System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(value).ToString(CultureInfo.InvariantCulture);
        }

        private static string PartId(Part part)
        {
            return "part_" + Identity(part);
        }

        private static string Name(NXObject value, string fallback)
        {
            string name = null;
            try
            {
                name = value.Name;
            }
            catch (Exception)
            {
            }
            return string.IsNullOrEmpty(name) ? fallback : name;
        }

        private Dictionary<string, object> Reference(NXObject value, string kind, Part part, string fallback)
        {
            return objects.Register(value, kind, Name(value, fallback), PartId(part)).ToDictionary();
        }

        private static Dictionary<string, object> Result(params object[] pairs)
        {
            var result = new Dictionary<string, object>();
            for (int index = 0; index < pairs.Length; index += 2)
            {
                result[(string)pairs[index]] = pairs[index + 1];
            }
            return result;
        }

        private void SwitchToModeling()
        {
            try
            {
                if (session.ApplicationName != "UG_APP_MODELING")
                {
                    session.ApplicationSwitchImmediate("UG_APP_MODELING");
                }
            }
            catch (Exception)
            {
            }
        }

        private Dictionary<string, object> Status(Dictionary<string, object> values)
        {
            Part part = WorkPart(false);
            return Result(
                "connected", true,
                "nx_version", nxVersion,
                "bridge_protocol", Protocol.Version,
                "active_part", part != null ? Reference(part, "part", part, "Part") : null);
        }

        private Dictionary<string, object> ListObjects(string kind)
        {
            Part part = WorkPart(true);
            IEnumerable collection = kind == "sketch" ? (IEnumerable)part.Sketches
                : kind == "body" ? (IEnumerable)part.Bodies : part.Features;
            string fallback = char.ToUpperInvariant(kind[0]) + kind.Substring(1);
            var references = new List<object>();
            foreach (NXObject value in collection)
            {
                references.Add(Reference(value, kind, part, fallback));
            }
            return Result("objects", references, "message", "Found " + references.Count + " " + kind + "(s).");
        }

        private Dictionary<string, object> CreatePart(Dictionary<string, object> values)
        {
            string units = (string)values["units"];
            string destination = workspace.EnsureInside((string)values["path"]);
            Directory.CreateDirectory(Path.GetDirectoryName(destination));
            string stem = Path.GetFileNameWithoutExtension(destination);
            FileNew builder = session.Parts.FileNew();
            NXObject committed;
            try
            {
                builder.TemplateFileName = units == "mm" ? "model-plain-1-mm-template.prt" : "model-plain-1-inch-template.prt";
                builder.Units = units == "mm" ? Part.Units.Millimeters : Part.Units.Inches;
                builder.NewFileName = destination;
                builder.DisplayPartOption = DisplayPartOption.AllowAdditional;
                committed = builder.Commit();
            }
            finally
            {
                builder.Destroy();
            }
            Part part = committed as Part ?? WorkPart(true);
            SwitchToModeling();
            return Result("part", Reference(part, "part", part, stem), "message", "Created part: " + Name(part, stem));
        }

        private Dictionary<string, object> OpenPart(Dictionary<string, object> values)
        {
            string source = workspace.EnsureInside((string)values["path"]);
            string stem = Path.GetFileNameWithoutExtension(source);
            if (!File.Exists(source))
            {
                throw new NxToolError("NX_FILE_NOT_FOUND", "Part file does not exist: " + Path.GetFileName(source));
            }
            PartLoadStatus loadStatus = null;
            BasePart opened;
            try
            {
                opened = session.Parts.OpenBaseDisplay(source, out loadStatus);
            }
            finally
            {
                if (loadStatus != null)
                {
                    loadStatus.Dispose();
                }
            }
            Part part = WorkPart(false) ?? (opened as Part);
            SwitchToModeling();
            return Result("part", Reference(part, "part", part, stem), "message", "Opened part: " + Name(part, stem));
        }

        private Dictionary<string, object> SavePart(Dictionary<string, object> values)
        {
            Part part = WorkPart(true);
            string path = part.FullPath;
            bool absolute = !string.IsNullOrEmpty(path) &&
                ((path.Length > 2 && path[1] == ':' && (path[2] == '\\' || path[2] == '/')) || path.StartsWith(@"\\"));
            if (!absolute)
            {
                throw Invalid("The work part has no absolute save path");
            }
            workspace.EnsureInside(path);
            PartSaveStatus saveStatus = null;
            try
            {
                saveStatus = part.Save(BasePart.SaveComponents.False, BasePart.CloseAfterSave.False);
            }
            finally
            {
                if (saveStatus != null)
                {
                    saveStatus.Dispose();
                }
            }
            undoMarks.Clear();
            return Result("message", "Saved part: " + Name(part, "Part"));
        }

        private Dictionary<string, object> ClosePart(Dictionary<string, object> values)
        {
            Part part = WorkPart(true);
            string partName = Name(part, "Part");
            string partId = PartId(part);
            if ((bool)values["save"])
            {
                SavePart(values);
            }
            part.Close(BasePart.CloseWholeTree.False, BasePart.CloseModified.CloseModified, null);
            objects.InvalidatePart(partId);
            unsafeParts.Remove(partId);
            undoMarks.Clear();
            return Result("message", "Closed part: " + partName);
        }

        private Dictionary<string, object> ExportStep(Dictionary<string, object> values)
        {
            Part part = WorkPart(true);
            string destination = workspace.EnsureInside((string)values["path"]);
            Directory.CreateDirectory(Path.GetDirectoryName(destination));
            // Translate into a temporary directory beside the destination and move the result
            // into place only once the translator wrote a non-empty file, so an earlier export
            // can never make a failed translation look successful. The file keeps its name
            // there: NX records that name in the STEP header. Same rule as nx_mcp.nx_bridge.
            string stagingDirectory = Path.Combine(
                Path.GetDirectoryName(destination),
                ".nx-mcp-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            string staging = Path.Combine(stagingDirectory, Path.GetFileName(destination));
            string log = Path.ChangeExtension(destination, ".log");
            Directory.CreateDirectory(stagingDirectory);
            try
            {
                TranslateStep(part, staging);
                if (!File.Exists(staging) || new FileInfo(staging).Length == 0)
                {
                    throw new NxToolError(
                        "NX_OPERATION_FAILED",
                        "The STEP translator wrote no output; see " +
                        Path.GetFileName(log) + " in the workspace.");
                }
                if (File.Exists(destination))
                {
                    File.Replace(staging, destination, null);
                }
                else
                {
                    File.Move(staging, destination);
                }
            }
            finally
            {
                // NX writes its translator log next to the output. Keep that log under the
                // destination's name, and never leave a partial export behind. Cleanup must not
                // mask the failure that brought us here.
                try
                {
                    string stagingLog = Path.ChangeExtension(staging, ".log");
                    if (File.Exists(stagingLog))
                    {
                        File.Delete(log);
                        File.Move(stagingLog, log);
                    }
                    Directory.Delete(stagingDirectory, true);
                }
                catch (IOException)
                {
                }
                catch (UnauthorizedAccessException)
                {
                }
            }
            return Result("path", destination, "message", "Exported STEP: " + Path.GetFileName(destination));
        }

        private void TranslateStep(Part part, string destination)
        {
            StepCreator builder = session.DexManager.CreateStepCreator();
            try
            {
                // NXOpen does not load the interactive STEP defaults (ugstep214.def): the layer
                // mask starts empty and every object type is off. A saved, unmodified part is
                // translated from InputFile; a modified part still exports its in-session model.
                builder.InputFile = part.FullPath;
                builder.LayerMask = "1-256";
                builder.ObjectTypes.Solids = true;
                builder.ObjectTypes.Surfaces = true;
                builder.ProcessHoldFlag = true;
                builder.OutputFile = destination;
                builder.Commit();
            }
            finally
            {
                builder.Destroy();
            }
        }

        private Dictionary<string, object> CreateSketch(Dictionary<string, object> values)
        {
            string plane = (string)values["plane"];
            string name = values["name"] as string;
            Vector3d normal = plane == "XY" ? new Vector3d(0.0, 0.0, 1.0)
                : plane == "XZ" ? new Vector3d(0.0, 1.0, 0.0) : new Vector3d(1.0, 0.0, 0.0);
            Part part = WorkPart(true);
            Plane placement = part.Planes.CreatePlane(
                new Point3d(0.0, 0.0, 0.0), normal, SmartObject.UpdateOption.WithinModeling);
            SketchInPlaceBuilder builder = part.Sketches.CreateSketchInPlaceBuilder2(null);
            NXObject committed;
            try
            {
                builder.PlaneReference = placement;
                committed = builder.Commit();
            }
            finally
            {
                builder.Destroy();
            }
            var sketch = (Sketch)committed;
            if (!string.IsNullOrEmpty(name))
            {
                sketch.SetName(name);
            }
            sketch.Activate(Sketch.ViewReorient.True);
            ObjectRef reference = objects.Register(
                sketch, "sketch", Name(sketch, string.IsNullOrEmpty(name) ? "Sketch" : name), PartId(part));
            return Result("object", reference.ToDictionary(), "message", "Created sketch: " + reference.Name);
        }

        private Sketch ResolveSketch(Dictionary<string, object> values, Part part)
        {
            return (Sketch)objects.Resolve((string)values["sketch_id"], "sketch", PartId(part));
        }

        private static double Coordinate(object point, string axis)
        {
            return Number(((Dictionary<string, object>)point)[axis]);
        }

        private Dictionary<string, object> AddLine(Sketch sketch, Part part, double x1, double y1, double x2, double y2)
        {
            Line line = part.Curves.CreateLine(new Point3d(x1, y1, 0.0), new Point3d(x2, y2, 0.0));
            sketch.AddGeometry(line, Sketch.InferConstraintsOption.InferNoConstraints);
            return Reference(line, "curve", part, "Line");
        }

        private Dictionary<string, object> SketchLine(Dictionary<string, object> values)
        {
            Part part = WorkPart(true);
            Sketch sketch = ResolveSketch(values, part);
            Dictionary<string, object> reference = AddLine(
                sketch,
                part,
                Coordinate(values["start"], "x"),
                Coordinate(values["start"], "y"),
                Coordinate(values["end"], "x"),
                Coordinate(values["end"], "y"));
            return Result("object", reference, "message", "Created line: " + reference["name"]);
        }

        private Dictionary<string, object> SketchRectangle(Dictionary<string, object> values)
        {
            Part part = WorkPart(true);
            Sketch sketch = ResolveSketch(values, part);
            double x1 = Coordinate(values["corner1"], "x");
            double y1 = Coordinate(values["corner1"], "y");
            double x2 = Coordinate(values["corner2"], "x");
            double y2 = Coordinate(values["corner2"], "y");
            if (x1 == x2 || y1 == y2)
            {
                throw Invalid("Rectangle width and height must be non-zero");
            }
            double[,] corners = { { x1, y1 }, { x2, y1 }, { x2, y2 }, { x1, y2 } };
            var references = new List<object>();
            for (int index = 0; index < 4; index++)
            {
                int next = (index + 1) % 4;
                references.Add(AddLine(sketch, part, corners[index, 0], corners[index, 1], corners[next, 0], corners[next, 1]));
            }
            return Result("objects", references, "message", "Created sketch rectangle");
        }

        private Dictionary<string, object> FinishSketch(Dictionary<string, object> values)
        {
            Part part = WorkPart(true);
            Sketch sketch = ResolveSketch(values, part);
            sketch.Deactivate(Sketch.ViewReorient.True, Sketch.UpdateLevel.Model);
            ObjectRef reference = objects.Register(sketch, "sketch", Name(sketch, "Sketch"), PartId(part));
            return Result("object", reference.ToDictionary(), "message", "Finished sketch: " + reference.Name);
        }

        private Dictionary<string, object> Extrude(Dictionary<string, object> values)
        {
            double distance = Number(values["distance"]);
            if (distance <= 0)
            {
                throw Invalid("distance must be greater than zero");
            }
            bool reverse = (bool)values["reverse"];
            Part part = WorkPart(true);
            Sketch sketch = ResolveSketch(values, part);
            var bodiesBefore = new HashSet<string>();
            foreach (Body existing in part.Bodies)
            {
                bodiesBefore.Add(Identity(existing));
            }
            Section section = part.Sections.CreateSection();
            SelectionIntentRuleOptions ruleOptions = part.ScRuleFactory.CreateRuleOptions();
            CurveFeatureRule rule = part.ScRuleFactory.CreateRuleCurveFeature(
                new Feature[] { sketch.Feature }, null, ruleOptions);
            ruleOptions.Dispose();
            section.AddToSection(
                new SelectionIntentRule[] { rule },
                null,
                null,
                null,
                new Point3d(0.0, 0.0, 0.0),
                Section.Mode.Create,
                false);
            Direction direction = part.Directions.CreateDirection(
                sketch, reverse ? Sense.Reverse : Sense.Forward, SmartObject.UpdateOption.WithinModeling);
            ExtrudeBuilder builder = part.Features.CreateExtrudeBuilder(null);
            Feature feature;
            try
            {
                builder.Section = section;
                builder.Direction = direction;
                builder.Limits.StartExtend.Value.RightHandSide = "0";
                builder.Limits.EndExtend.Value.RightHandSide = FormatNumber(values["distance"]);
                builder.BooleanOperation.Type = BooleanOperation.BooleanType.Create;
                builder.AllowSelfIntersectingSection(true);
                feature = builder.CommitFeature();
            }
            finally
            {
                builder.Destroy();
            }
            Body body = null;
            var bodyFeature = feature as BodyFeature;
            if (bodyFeature != null && bodyFeature.GetBodies().Length > 0)
            {
                body = bodyFeature.GetBodies()[0];
            }
            else
            {
                foreach (Body candidate in part.Bodies)
                {
                    if (!bodiesBefore.Contains(Identity(candidate)))
                    {
                        body = candidate;
                        break;
                    }
                }
            }
            if (body == null)
            {
                throw new NxToolError("NX_OPERATION_FAILED", "Extrude did not create a body");
            }
            return Result(
                "feature", Reference(feature, "feature", part, "Extrude"),
                "body", Reference(body, "body", part, "Body"),
                "message", "Extruded " + Name(sketch, "Sketch") + " by " + FormatNumber(values["distance"]));
        }

        private Dictionary<string, object> Undo(Dictionary<string, object> values)
        {
            if (undoMarks.Count == 0)
            {
                throw new NxToolError("NX_UNDO_UNAVAILABLE", "No NX MCP operation is available to undo");
            }
            Part part = WorkPart(true);
            session.UndoToMark(undoMarks[undoMarks.Count - 1], null);
            undoMarks.RemoveAt(undoMarks.Count - 1);
            objects.InvalidatePart(PartId(part));
            return Result("message", "Undo successful");
        }

        private Dictionary<string, object> FitView(Dictionary<string, object> values)
        {
            WorkPart(true).ModelingViews.WorkView.Fit();
            return Result("message", "View fitted");
        }
    }

    /// <summary>Runs executor calls on NX's UI thread, only while NX is idle.</summary>
    internal sealed class MainThreadDispatcher
    {
        private enum CallState
        {
            Queued,
            Running,
            Done,
            Cancelled,
        }

        private sealed class PendingCall
        {
            public string Method;
            public Dictionary<string, object> Parameters;
            public CallState State = CallState.Queued;
            public Dictionary<string, object> Result;
            public Exception Error;
            public string BusyReason;
            public readonly ManualResetEvent Done = new ManualResetEvent(false);
            public readonly AutoResetEvent Busy = new AutoResetEvent(false);
        }

        public static readonly TimeSpan QueueTimeout = TimeSpan.FromSeconds(60);
        public static readonly TimeSpan ExecutionTimeout = TimeSpan.FromSeconds(110);
        private static readonly TimeSpan BusyRetry = TimeSpan.FromMilliseconds(250);

        private readonly Control marshal;
        private readonly Func<string, Dictionary<string, object>, Dictionary<string, object>> executor;
        private readonly Action callCompleted;
        private readonly ManualResetEvent stopped = new ManualResetEvent(false);
        private volatile bool stopping;
        private bool executing;

        /// <param name="callCompleted">
        /// Runs on the UI thread after a call returned, so a stop requested during that call can
        /// finish without reposting itself while the call is still on the stack.
        /// </param>
        public MainThreadDispatcher(
            Control marshal,
            Func<string, Dictionary<string, object>, Dictionary<string, object>> executor,
            Action callCompleted)
        {
            this.marshal = marshal;
            this.executor = executor;
            this.callCompleted = callCompleted;
        }

        /// <summary>True while an NXOpen call runs; only meaningful on the UI thread.</summary>
        public bool Executing
        {
            get { return executing; }
        }

        public void Stop()
        {
            stopping = true;
            stopped.Set();
        }

        /// <summary>Called on the socket thread; blocks until the UI thread has run the call.</summary>
        public Dictionary<string, object> Call(string method, Dictionary<string, object> parameters)
        {
            var pending = new PendingCall { Method = method, Parameters = parameters };
            Stopwatch clock = Stopwatch.StartNew();
            bool post = true;
            while (true)
            {
                if (stopping)
                {
                    lock (pending)
                    {
                        if (pending.State == CallState.Queued)
                        {
                            pending.State = CallState.Cancelled;
                            throw Unavailable();
                        }
                    }
                }
                if (post)
                {
                    post = false;
                    if (!Post(pending))
                    {
                        lock (pending)
                        {
                            if (pending.State == CallState.Queued)
                            {
                                pending.State = CallState.Cancelled;
                                throw Unavailable();
                            }
                        }
                    }
                }
                double remaining = (QueueTimeout - clock.Elapsed).TotalMilliseconds;
                int signaled = WaitHandle.WaitAny(
                    new WaitHandle[] { pending.Done, pending.Busy, stopped },
                    (int)Math.Max(0, Math.Min(remaining, 500)));
                CallState state;
                lock (pending)
                {
                    state = pending.State;
                    if (state == CallState.Queued && clock.Elapsed >= QueueTimeout)
                    {
                        pending.State = CallState.Cancelled;
                        throw Timeout(false, pending.BusyReason);
                    }
                }
                if (state == CallState.Done)
                {
                    break;
                }
                if (state == CallState.Running)
                {
                    TimeSpan left = ExecutionTimeout - clock.Elapsed;
                    if (!pending.Done.WaitOne(left > TimeSpan.Zero ? left : TimeSpan.Zero))
                    {
                        throw Timeout(true, null);
                    }
                    break;
                }
                if (signaled == 1)
                {
                    // NX refused the UI lock: a dialog or command is active. Ask again shortly.
                    Thread.Sleep(BusyRetry);
                    post = true;
                }
            }
            if (pending.Error != null)
            {
                throw pending.Error;
            }
            if (pending.Result == null)
            {
                throw new NxToolError("NX_OPERATION_FAILED", "NX bridge returned no result");
            }
            return pending.Result;
        }

        private bool Post(PendingCall pending)
        {
            try
            {
                marshal.BeginInvoke(new MethodInvoker(delegate { Attempt(pending); }));
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void Attempt(PendingCall pending)
        {
            lock (pending)
            {
                if (pending.State != CallState.Queued)
                {
                    return;
                }
            }
            if (executing)
            {
                ReportBusy(pending, "NX MCP is already running a request");
                return;
            }
            UFSession uf = UFSession.GetUFSession();
            int lockStatus;
            try
            {
                lockStatus = uf.Ui.LockUgAccess(UFConstants.UF_UI_FROM_CUSTOM);
            }
            catch (Exception)
            {
                lockStatus = -1;
            }
            if (lockStatus != UFConstants.UF_UI_LOCK_SET)
            {
                ReportBusy(pending, "NX is busy with a dialog or command");
                return;
            }
            lock (pending)
            {
                if (pending.State != CallState.Queued)
                {
                    Unlock(uf);
                    return;
                }
                pending.State = CallState.Running;
            }
            executing = true;
            Dictionary<string, object> result = null;
            Exception error = null;
            Stopwatch clock = Stopwatch.StartNew();
            try
            {
                BridgeHost.SetStatus("NX MCP: " + pending.Method);
                result = executor(pending.Method, pending.Parameters);
            }
            catch (Exception caught)
            {
                error = caught;
            }
            finally
            {
                executing = false;
                Unlock(uf);
                BridgeHost.SetStatus(BridgeHost.ReadyStatus);
            }
            var failure = error as NxToolError;
            BridgeHost.Log(pending.Method + " " + (error == null ? "ok" : failure != null ? failure.Code : error.GetType().Name) +
                " " + clock.ElapsedMilliseconds + " ms");
            lock (pending)
            {
                pending.Result = result;
                pending.Error = error;
                pending.State = CallState.Done;
            }
            pending.Done.Set();
            if (callCompleted != null)
            {
                try
                {
                    callCompleted();
                }
                catch (Exception caught)
                {
                    // Never let this escape into NX's message loop.
                    BridgeHost.Log("after-call handler failed: " + caught.Message);
                }
            }
        }

        private static void Unlock(UFSession uf)
        {
            try
            {
                uf.Ui.UnlockUgAccess(UFConstants.UF_UI_FROM_CUSTOM);
            }
            catch (Exception)
            {
            }
        }

        private static void ReportBusy(PendingCall pending, string reason)
        {
            lock (pending)
            {
                pending.BusyReason = reason;
            }
            pending.Busy.Set();
        }

        private static NxToolError Unavailable()
        {
            return new NxToolError(
                "NX_BRIDGE_UNAVAILABLE", "NX bridge is stopping.", null, null, true, NxToolError.State("not_started"));
        }

        private static NxToolError Timeout(bool started, string busyReason)
        {
            var error = new NxToolError(
                "NX_MAIN_THREAD_UNAVAILABLE",
                "NX did not complete the request before the timeout.",
                null,
                null,
                !started,
                NxToolError.State(started ? "unknown" : "not_started"));
            if (!started && busyReason != null)
            {
                error.Suggestion = busyReason + "; finish or cancel it in NX, then retry.";
            }
            return error;
        }
    }

    /// <summary>Serialized loopback JSON-RPC listener (mirrors nx_mcp.bridge.BridgeServer).</summary>
    internal sealed class BridgeServer
    {
        private readonly MainThreadDispatcher dispatcher;
        private readonly string token;
        private readonly string stopFile;
        private readonly Action stopRequested;
        private readonly object connectionLock = new object();
        private TcpListener listener;
        private Thread thread;
        private Socket connection;
        private volatile bool stopping;

        public BridgeServer(MainThreadDispatcher dispatcher, string token, string stopFile, Action stopRequested)
        {
            this.dispatcher = dispatcher;
            this.token = token;
            this.stopFile = stopFile;
            this.stopRequested = stopRequested;
        }

        public int Port { get; private set; }

        public void Start()
        {
            listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start(4);
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            thread = new Thread(Serve);
            thread.IsBackground = true;
            thread.Name = "nx-mcp-gui-bridge";
            thread.Start();
        }

        public void Stop()
        {
            lock (connectionLock)
            {
                stopping = true;
                if (connection != null)
                {
                    try
                    {
                        connection.Shutdown(SocketShutdown.Both);
                    }
                    catch (Exception)
                    {
                    }
                }
            }
            try
            {
                listener.Stop();
            }
            catch (Exception)
            {
            }
            if (thread != null && thread != Thread.CurrentThread)
            {
                thread.Join(TimeSpan.FromSeconds(2));
            }
        }

        /// <summary>
        /// Stops taking new work but leaves the active connection alone, so a response the socket
        /// thread still holds goes out in full. Blocks until that request is finished.
        /// </summary>
        public void StopAfterResponse()
        {
            stopping = true;
            try
            {
                listener.Stop();
            }
            catch (Exception)
            {
            }
            if (thread != null && thread != Thread.CurrentThread)
            {
                thread.Join(TimeSpan.FromSeconds(10));
            }
        }

        private void Serve()
        {
            Stopwatch stopCheck = Stopwatch.StartNew();
            bool stopSignalled = false;
            while (!stopping)
            {
                try
                {
                    if (!stopSignalled && stopFile != null && stopCheck.ElapsedMilliseconds >= 250)
                    {
                        stopCheck.Restart();
                        if (File.Exists(stopFile))
                        {
                            stopSignalled = true;
                            stopRequested();
                        }
                    }
                    if (!listener.Pending())
                    {
                        Thread.Sleep(20);
                        continue;
                    }
                    Socket client = listener.AcceptSocket();
                    lock (connectionLock)
                    {
                        if (stopping)
                        {
                            client.Close();
                            return;
                        }
                        connection = client;
                    }
                    try
                    {
                        Respond(client);
                    }
                    finally
                    {
                        lock (connectionLock)
                        {
                            connection = null;
                        }
                        client.Close();
                    }
                }
                catch (Exception error)
                {
                    if (stopping)
                    {
                        return;
                    }
                    BridgeHost.Log("listener error: " + error.Message);
                    Thread.Sleep(100);
                }
            }
        }

        private void Respond(Socket client)
        {
            object requestId = null;
            bool started = false;
            var response = new Dictionary<string, object>();
            response["jsonrpc"] = "2.0";
            response["protocol_version"] = Protocol.Version;
            response["id"] = null;
            try
            {
                byte[] raw = Receive(client);
                if (raw == null)
                {
                    return;
                }
                Dictionary<string, object> request = Decode(raw);
                object candidate;
                request.TryGetValue("id", out candidate);
                if (!(candidate is string || candidate is long))
                {
                    throw new NxToolError("NX_INVALID_REQUEST", "Bridge request ID is invalid");
                }
                requestId = candidate;
                response["id"] = requestId;
                object jsonrpc;
                request.TryGetValue("jsonrpc", out jsonrpc);
                if (!"2.0".Equals(jsonrpc))
                {
                    throw new NxToolError("NX_PROTOCOL_ERROR", "Expected JSON-RPC 2.0");
                }
                object version;
                request.TryGetValue("protocol_version", out version);
                if (!(version is long) || (long)version != Protocol.Version)
                {
                    throw new NxToolError("NX_PROTOCOL_VERSION_MISMATCH", "Bridge protocol version does not match");
                }
                object offered;
                request.TryGetValue("token", out offered);
                if (!TokenMatches(offered as string))
                {
                    throw new NxToolError("NX_AUTH_FAILED", "Bridge authentication failed");
                }
                object method;
                request.TryGetValue("method", out method);
                object parameters = request.ContainsKey("params") ? request["params"] : new Dictionary<string, object>();
                if (!(method is string) || ((string)method).Length == 0 || !(parameters is Dictionary<string, object>))
                {
                    throw new NxToolError("NX_INVALID_REQUEST", "Bridge method and params are invalid");
                }
                started = true;
                Dictionary<string, object> result = dispatcher.Call((string)method, (Dictionary<string, object>)parameters);
                response["ok"] = true;
                response["result"] = result;
            }
            catch (NxToolError error)
            {
                if (!started)
                {
                    error.Details["execution_state"] = "not_started";
                }
                response["ok"] = false;
                response["error"] = error.ToDictionary();
            }
            catch (Exception error)
            {
                bool protocolError = error is IOException || error is SocketException || error is TimeoutException ||
                    error is ArgumentException || error is FormatException || error is ObjectDisposedException;
                response["ok"] = false;
                response["error"] = protocolError
                    ? new NxToolError(
                        "NX_PROTOCOL_ERROR",
                        error.Message,
                        null,
                        null,
                        false,
                        NxToolError.State(started ? "unknown" : "not_started")).ToDictionary()
                    : new NxToolError(
                        "NX_OPERATION_FAILED",
                        "Bridge execution failed",
                        null,
                        null,
                        false,
                        NxToolError.State("unknown")).ToDictionary();
            }
            byte[] encoded = Encode(response, requestId, started);
            try
            {
                client.SendTimeout = (int)Protocol.ReadTimeout.TotalMilliseconds;
                // Socket.Send may take only part of the buffer. A truncated response would leave
                // the sidecar unable to tell whether the command ran, so send all of it.
                int sent = 0;
                while (sent < encoded.Length)
                {
                    int written = client.Send(
                        encoded, sent, encoded.Length - sent, SocketFlags.None);
                    if (written <= 0)
                    {
                        break;
                    }
                    sent += written;
                }
            }
            catch (Exception)
            {
            }
        }

        private bool TokenMatches(string offered)
        {
            if (offered == null)
            {
                return false;
            }
            foreach (char character in offered)
            {
                if (character > 0x7f)
                {
                    return false;
                }
            }
            int difference = offered.Length ^ token.Length;
            for (int index = 0; index < token.Length; index++)
            {
                difference |= token[index] ^ (index < offered.Length ? offered[index] : 0);
            }
            return difference == 0;
        }

        private byte[] Receive(Socket client)
        {
            Stopwatch clock = Stopwatch.StartNew();
            var buffer = new MemoryStream();
            var chunk = new byte[65536];
            while (Array.IndexOf(buffer.GetBuffer(), (byte)'\n', 0, (int)buffer.Length) < 0)
            {
                double remaining = (Protocol.ReadTimeout - clock.Elapsed).TotalMilliseconds;
                if (remaining <= 0)
                {
                    throw new TimeoutException("Bridge request receive deadline expired");
                }
                if (stopping)
                {
                    return null;
                }
                client.ReceiveTimeout = (int)Math.Max(1, Math.Min(remaining, 100));
                int received;
                try
                {
                    received = client.Receive(
                        chunk, 0, (int)Math.Min(chunk.Length, Protocol.MaxMessageBytes + 1 - buffer.Length), SocketFlags.None);
                }
                catch (SocketException error)
                {
                    if (error.SocketErrorCode == SocketError.TimedOut)
                    {
                        continue;
                    }
                    throw;
                }
                if (received == 0)
                {
                    throw new NxToolError("NX_PROTOCOL_ERROR", "Incomplete bridge request");
                }
                buffer.Write(chunk, 0, received);
                if (buffer.Length > Protocol.MaxMessageBytes)
                {
                    throw new NxToolError("NX_REQUEST_TOO_LARGE", "Bridge request is too large");
                }
            }
            return buffer.ToArray();
        }

        private static Dictionary<string, object> Decode(byte[] raw)
        {
            object payload;
            try
            {
                payload = Json.Parse(Protocol.Utf8.GetString(raw));
            }
            catch (Exception)
            {
                throw new NxToolError("NX_PROTOCOL_ERROR", "Invalid bridge JSON");
            }
            var request = payload as Dictionary<string, object>;
            if (request == null)
            {
                throw new NxToolError("NX_PROTOCOL_ERROR", "Bridge message must be an object");
            }
            return request;
        }

        private static byte[] Encode(Dictionary<string, object> response, object requestId, bool started)
        {
            try
            {
                byte[] encoded = Protocol.Utf8.GetBytes(Json.Serialize(response) + "\n");
                if (encoded.Length > Protocol.MaxMessageBytes)
                {
                    throw new ArgumentException("Bridge response is too large");
                }
                return encoded;
            }
            catch (Exception)
            {
                var fallback = new Dictionary<string, object>();
                fallback["jsonrpc"] = "2.0";
                fallback["protocol_version"] = Protocol.Version;
                fallback["id"] = requestId;
                fallback["ok"] = false;
                fallback["error"] = new NxToolError(
                    "NX_PROTOCOL_ERROR",
                    "Bridge response cannot be encoded within the size limit",
                    null,
                    null,
                    false,
                    NxToolError.State(started ? "unknown" : "not_started")).ToDictionary();
                return Protocol.Utf8.GetBytes(Json.Serialize(fallback) + "\n");
            }
        }
    }

    /// <summary>Bridge lifecycle, configuration, descriptor and user feedback. UI thread only.</summary>
    internal static class BridgeHost
    {
        public const string ReadyStatus = "NX MCP bridge ready";
        private const string ConfigName = "gui-bridge.json";
        private static readonly object LogLock = new object();
        private static readonly string DefaultStateDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "nx-mcp");

        private static string stateDirectory = DefaultStateDirectory;
        private static string workspaceRoot;
        private static Control marshal;
        private static MainThreadDispatcher dispatcher;
        private static BridgeServer server;
        private static string token;
        private static bool exitHooked;
        private static string pendingStopReason;
        private static volatile bool stopInProgress;

        public static bool Running
        {
            get { return server != null; }
        }

        private static string DescriptorPath
        {
            get { return Path.Combine(stateDirectory, "bridge.json"); }
        }

        private static string LogPath
        {
            get { return Path.Combine(stateDirectory, "gui-bridge.log"); }
        }

        public static void StartFromStartup()
        {
            try
            {
                Start();
            }
            catch (Exception error)
            {
                Log("start failed: " + error.Message);
                SetStatus("NX MCP bridge did not start: " + error.Message);
            }
        }

        public static void Toggle()
        {
            if (Running)
            {
                Stop("run again from NX");
                Show(NXMessageBox.DialogType.Information, "NX MCP bridge stopped.");
                return;
            }
            try
            {
                Start();
                Show(NXMessageBox.DialogType.Information, "NX MCP bridge is running on 127.0.0.1:" + server.Port +
                    ".\nWorkspace: " + workspaceRoot + "\nDescriptor: " + DescriptorPath +
                    "\nRun this file again to stop it.");
            }
            catch (Exception error)
            {
                Log("start failed: " + error.Message);
                Show(NXMessageBox.DialogType.Error, "NX MCP bridge did not start:\n" + error.Message);
            }
        }

        private static void Start()
        {
            if (Running)
            {
                return;
            }
            pendingStopReason = null;
            stopInProgress = false;
            Dictionary<string, object> config = LoadConfig();
            string root = Setting(config, "NX_MCP_WORKSPACE", "workspace");
            if (string.IsNullOrEmpty(root))
            {
                throw new InvalidOperationException(
                    "Set NX_MCP_WORKSPACE or create " + ConfigCandidates()[0] +
                    " containing {\"workspace\": \"C:\\\\NX_MCP_WORKSPACE\"}.");
            }
            string state = Setting(config, "NX_MCP_STATE_DIR", "state_dir");
            if (string.IsNullOrEmpty(state))
            {
                stateDirectory = DefaultStateDirectory;
            }
            else
            {
                // A relative value would resolve against NX's working directory, which the
                // sidecar does not share, so the two sides could disagree on the path.
                if (!IsFullyQualified(state))
                {
                    throw new InvalidOperationException(
                        "state_dir (or NX_MCP_STATE_DIR) must be an absolute path so that the " +
                        "bridge and the sidecar agree on it: " + state);
                }
                stateDirectory = Path.GetFullPath(state);
            }
            var workspace = new Workspace(root);
            Directory.CreateDirectory(workspace.Root);
            CreateProtectedDirectory(stateDirectory);
            using (FileStream startupLock = AcquireDescriptorStartupLock())
            {
                RefuseLiveDescriptor();
                string stopFile = Setting(config, "NX_MCP_BRIDGE_STOP_FILE", "stop_file");
                if (!string.IsNullOrEmpty(stopFile) && File.Exists(stopFile))
                {
                    File.Delete(stopFile);
                }

                Session session = Session.GetSession();
                string nxVersion = null;
                try
                {
                    nxVersion = session.GetEnvironmentVariableValue("UGII_VERSION");
                }
                catch (Exception)
                {
                }
                if (string.IsNullOrEmpty(nxVersion))
                {
                    nxVersion = "NX build unknown";
                }

                var control = new Control();
                MainThreadDispatcher callDispatcher = null;
                BridgeServer listener = null;
                IntPtr handle;
                string secret;
                try
                {
                    handle = control.Handle;  // bind the marshaling window to this (UI) thread
                    var executor = new Executor(session, nxVersion, workspace);
                    callDispatcher = new MainThreadDispatcher(control, executor.Execute, CompletePendingStop);
                    secret = NewToken();
                    listener = new BridgeServer(
                        callDispatcher,
                        secret,
                        string.IsNullOrEmpty(stopFile) ? null : stopFile,
                        delegate { control.BeginInvoke(new MethodInvoker(delegate { Stop("stop file"); })); });
                    listener.Start();
                    WriteDescriptor(listener.Port, secret, nxVersion);
                }
                catch (Exception)
                {
                    // Attempt every cleanup without hiding the startup failure.
                    try { if (callDispatcher != null) { callDispatcher.Stop(); } }
                    catch (Exception) { }
                    try { if (listener != null) { listener.Stop(); } }
                    catch (Exception) { }
                    try { control.Dispose(); }
                    catch (Exception) { }
                    throw;
                }
                marshal = control;
                dispatcher = callDispatcher;
                server = listener;
                token = secret;
                workspaceRoot = workspace.Root;
                if (!exitHooked)
                {
                    exitHooked = true;
                    AppDomain.CurrentDomain.ProcessExit += delegate { RemoveDescriptor(); };
                    AppDomain.CurrentDomain.DomainUnload += delegate { RemoveDescriptor(); };
                }
                Log("started on 127.0.0.1:" + listener.Port + " (NX " + nxVersion + ", pid " +
                    Process.GetCurrentProcess().Id + ", handle " + handle + "), workspace " + workspace.Root +
                    ", descriptor " + DescriptorPath);
            }
            SetStatus(ReadyStatus);
        }

        public static void Stop(string reason)
        {
            if (!Running || stopInProgress)
            {
                return;
            }
            if (dispatcher.Executing)
            {
                // Reentered from an NX message pump while a call runs. Reposting here can run
                // again before that call returns, so only refuse new work now and let the
                // dispatcher hand the shutdown back once the call is done.
                if (pendingStopReason == null)
                {
                    pendingStopReason = reason;
                    dispatcher.Stop();
                    Log("stop deferred until the running call finishes (" + reason + ")");
                }
                return;
            }
            dispatcher.Stop();
            // No request is in flight here, so the active connection can go straight away.
            server.Stop();
            FinishStop(reason);
        }

        /// <summary>Runs on the UI thread once a call returned: completes a deferred stop.</summary>
        private static void CompletePendingStop()
        {
            if (pendingStopReason == null || stopInProgress || !Running || dispatcher.Executing)
            {
                return;
            }
            stopInProgress = true;
            string reason = pendingStopReason;
            Control control = marshal;
            BridgeServer listener = server;
            // The socket thread still has to encode and send this call's response, and cutting the
            // connection here would leave the sidecar unsure whether the command ran. Wait for
            // that off the UI thread, then finish teardown back on it: the marshaling control has
            // to be disposed by the thread that owns it.
            var closer = new Thread(new ThreadStart(delegate
            {
                listener.StopAfterResponse();
                try
                {
                    control.BeginInvoke(new MethodInvoker(delegate { FinishStop(reason); }));
                }
                catch (Exception)
                {
                }
            }));
            closer.IsBackground = true;
            closer.Name = "nx-mcp-bridge-stop";
            closer.Start();
        }

        /// <summary>Releases the bridge on the UI thread. Safe to reach only once per start.</summary>
        private static void FinishStop(string reason)
        {
            if (marshal == null)
            {
                return;
            }
            RemoveDescriptor();
            marshal.Dispose();
            marshal = null;
            dispatcher = null;
            server = null;
            token = null;
            pendingStopReason = null;
            stopInProgress = false;
            SetStatus("NX MCP bridge stopped");
            Log("stopped (" + reason + ")");
        }

        /// <summary>
        /// Config beside the DLL wins: an MSIX-packaged MCP client (such as a Store-installed
        /// desktop app) and an NX started from Explorer see different copies of AppData.
        /// </summary>
        private static string[] ConfigCandidates()
        {
            // The add-in folder is where this assembly was loaded from. The application domain
            // base directory belongs to NX, not to the DLL.
            string addInDirectory = null;
            try
            {
                string location = typeof(BridgeHost).Assembly.Location;
                if (!string.IsNullOrEmpty(location))
                {
                    addInDirectory = Path.GetDirectoryName(location);
                }
            }
            catch (Exception)
            {
            }
            if (string.IsNullOrEmpty(addInDirectory))
            {
                addInDirectory = AppDomain.CurrentDomain.BaseDirectory;
            }
            return new[] { Path.Combine(addInDirectory, ConfigName), Path.Combine(DefaultStateDirectory, ConfigName) };
        }

        /// <summary>
        /// True for a drive-rooted or UNC path. Path.IsPathRooted also accepts a drive-relative
        /// path such as C:state, which still depends on the working directory.
        /// </summary>
        private static bool IsFullyQualified(string path)
        {
            if (path.Length >= 2 && (path[0] == '\\' || path[0] == '/') &&
                (path[1] == '\\' || path[1] == '/'))
            {
                return true;
            }
            return path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':' &&
                (path[2] == '\\' || path[2] == '/');
        }

        private static Dictionary<string, object> LoadConfig()
        {
            foreach (string candidate in ConfigCandidates())
            {
                if (File.Exists(candidate))
                {
                    var config = Json.Parse(File.ReadAllText(candidate, Encoding.UTF8)) as Dictionary<string, object>;
                    if (config == null)
                    {
                        throw new InvalidOperationException(candidate + " must contain a JSON object.");
                    }
                    return config;
                }
            }
            return new Dictionary<string, object>();
        }

        private static string Setting(Dictionary<string, object> config, string environmentName, string configKey)
        {
            string value = Environment.GetEnvironmentVariable(environmentName);
            if (!string.IsNullOrEmpty(value))
            {
                return value;
            }
            object configured;
            return config.TryGetValue(configKey, out configured) ? configured as string : null;
        }

        private static string NewToken()
        {
            var bytes = new byte[32];
            using (var random = new RNGCryptoServiceProvider())
            {
                random.GetBytes(bytes);
            }
            var builder = new StringBuilder(64);
            foreach (byte value in bytes)
            {
                builder.Append(value.ToString("x2", CultureInfo.InvariantCulture));
            }
            return builder.ToString();
        }

        /// <summary>Serializes descriptor validation, listener startup and publication across NX processes.</summary>
        private static FileStream AcquireDescriptorStartupLock()
        {
            string path = Path.Combine(stateDirectory, "bridge.lock");
            var elapsed = Stopwatch.StartNew();
            while (true)
            {
                try
                {
                    // Never delete the lock file: its open handle, not its existence, owns the lock.
                    return new FileStream(
                        path, FileMode.OpenOrCreate,
                        FileSystemRights.ReadData | FileSystemRights.WriteData | FileSystemRights.Synchronize,
                        FileShare.None, 4096, FileOptions.None, UserOnlyFileSecurity());
                }
                catch (IOException error)
                {
                    int code = System.Runtime.InteropServices.Marshal.GetHRForException(error) & 0xffff;
                    if (code != 32 && code != 33)  // sharing/lock violation only
                    {
                        throw;
                    }
                    if (elapsed.ElapsedMilliseconds >= 5000)
                    {
                        throw new TimeoutException(
                            "Timed out waiting for NX MCP bridge startup lock; another NX process may be starting the bridge.",
                            error);
                    }
                    Thread.Sleep(50);
                }
            }
        }

        /// <summary>Never replace a descriptor whose bridge still answers on its port.</summary>
        private static void RefuseLiveDescriptor()
        {
            if (!File.Exists(DescriptorPath))
            {
                return;
            }
            Dictionary<string, object> existing;
            try
            {
                existing = Json.Parse(File.ReadAllText(DescriptorPath, Encoding.UTF8)) as Dictionary<string, object>;
            }
            catch (Exception)
            {
                return;
            }
            object port;
            if (existing == null || !existing.TryGetValue("port", out port) || !(port is long))
            {
                return;
            }
            long candidate = (long)port;
            if (candidate < 1 || candidate > 65535)
            {
                Log("ignoring a stale descriptor: port " + candidate + " is out of range");
                return;
            }
            if (!IsForeignPort((int)candidate))
            {
                object pid;
                existing.TryGetValue("pid", out pid);
                throw new InvalidOperationException(
                    "Another NX MCP bridge is already running (pid " + pid + ", port " + port +
                    "). Delete " + DescriptorPath + " if that bridge is gone.");
            }
            Log("ignoring a stale descriptor: port " + port + " does not answer as an NX MCP bridge");
        }

        /// <summary>
        /// True only when the descriptor's port provably belongs to something else, so a
        /// descriptor another process has taken over cannot keep this bridge from starting.
        /// Nothing listening, or an answer that is not a bridge response, is proof. A port that
        /// accepts and stays silent is not: the bridge behind it may be busy, and overwriting its
        /// descriptor would strand its sidecar.
        /// </summary>
        private static bool IsForeignPort(int port)
        {
            try
            {
                using (var probe = new TcpClient())
                {
                    IAsyncResult attempt = probe.BeginConnect(IPAddress.Loopback, port, null, null);
                    if (!attempt.AsyncWaitHandle.WaitOne(300) || !probe.Connected)
                    {
                        return true;
                    }
                    probe.EndConnect(attempt);
                    var request = new Dictionary<string, object>();
                    request["jsonrpc"] = "2.0";
                    request["id"] = "nx-mcp-descriptor-probe";
                    request["protocol_version"] = Protocol.Version;
                    // Deliberately invalid: a bridge answers NX_AUTH_FAILED from its socket
                    // thread, so a busy bridge replies without waiting for NX's UI thread.
                    request["token"] = "nx-mcp-descriptor-probe";
                    request["method"] = "nx_status";
                    request["params"] = new Dictionary<string, object>();
                    byte[] payload = Protocol.Utf8.GetBytes(Json.Serialize(request) + "\n");
                    NetworkStream stream = probe.GetStream();
                    stream.WriteTimeout = 1000;
                    stream.ReadTimeout = 2000;
                    var answer = new StringBuilder();
                    var buffer = new byte[4096];
                    bool wrote = false;
                    try
                    {
                        stream.Write(payload, 0, payload.Length);
                        wrote = true;
                        while (answer.Length < 64 * 1024 && answer.ToString().IndexOf('\n') < 0)
                        {
                            int read = stream.Read(buffer, 0, buffer.Length);
                            if (read <= 0)
                            {
                                break;
                            }
                            answer.Append(Protocol.Utf8.GetString(buffer, 0, read));
                        }
                    }
                    catch (IOException)
                    {
                        // A peer that was already gone is not a bridge. Silence after the request
                        // went out proves nothing, because a busy bridge answers late.
                        return !wrote;
                    }
                    if (answer.Length == 0)
                    {
                        return true;
                    }
                    Dictionary<string, object> reply;
                    try
                    {
                        reply = Json.Parse(answer.ToString().Trim()) as Dictionary<string, object>;
                    }
                    catch (Exception)
                    {
                        return true;
                    }
                    return !(reply != null && reply.ContainsKey("ok") &&
                        (reply.ContainsKey("error") || reply.ContainsKey("result")));
                }
            }
            catch (SocketException)
            {
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static void WriteDescriptor(int port, string secret, string nxVersion)
        {
            CreateProtectedDirectory(stateDirectory);
            var descriptor = new Dictionary<string, object>();
            descriptor["port"] = port;
            descriptor["token"] = secret;
            descriptor["pid"] = Process.GetCurrentProcess().Id;
            descriptor["nx_version"] = nxVersion;
            descriptor["protocol_version"] = Protocol.Version;
            descriptor["host"] = "127.0.0.1";
            string temporary = DescriptorPath + ".tmp." + Process.GetCurrentProcess().Id + "." +
                Guid.NewGuid().ToString("N");
            try
            {
                byte[] payload = new UTF8Encoding(false).GetBytes(Json.Serialize(descriptor));
                // The token is protected before the temporary file contains any data.
                using (var stream = new FileStream(
                    temporary,
                    FileMode.CreateNew,
                    FileSystemRights.WriteData | FileSystemRights.Synchronize,
                    FileShare.None,
                    4096,
                    FileOptions.None,
                    UserOnlyFileSecurity()))
                {
                    stream.Write(payload, 0, payload.Length);
                }
                if (File.Exists(DescriptorPath))
                {
                    // Replace preserves destination ACLs. Protect the stale destination first,
                    // so an ACL failure cannot occur after publishing this listener's descriptor.
                    File.SetAccessControl(DescriptorPath, UserOnlyFileSecurity());
                    File.Replace(temporary, DescriptorPath, null);
                }
                else
                {
                    File.Move(temporary, DescriptorPath);
                }
            }
            finally
            {
                // Only our unique temporary is eligible; cleanup must not hide the original error.
                try { File.Delete(temporary); }
                catch (Exception) { }
            }
        }

        /// <summary>Allows this account alone, with inherited rules turned off.</summary>
        private static FileSecurity UserOnlyFileSecurity()
        {
            var security = new FileSecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new FileSystemAccessRule(
                WindowsIdentity.GetCurrent().User,
                FileSystemRights.FullControl,
                AccessControlType.Allow));
            return security;
        }

        /// <summary>
        /// Creates a missing state directory for this account alone. An existing directory keeps
        /// the rules it already has, so a shared one stays the caller's choice to fix.
        /// </summary>
        private static void CreateProtectedDirectory(string path)
        {
            if (Directory.Exists(path))
            {
                return;
            }
            var security = new DirectorySecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new FileSystemAccessRule(
                WindowsIdentity.GetCurrent().User,
                FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                PropagationFlags.None,
                AccessControlType.Allow));
            Directory.CreateDirectory(path, security);
        }

        private static void RemoveDescriptor()
        {
            try
            {
                if (token == null)
                {
                    return;
                }
                // Serialize the token check and delete with replacement by a new startup.
                using (FileStream ownershipLock = AcquireDescriptorStartupLock())
                {
                    string secret = token;
                    if (secret == null || !File.Exists(DescriptorPath))
                    {
                        return;
                    }
                    var current = Json.Parse(File.ReadAllText(DescriptorPath, Encoding.UTF8)) as Dictionary<string, object>;
                    object stored;
                    if (current != null && current.TryGetValue("token", out stored) && secret.Equals(stored))
                    {
                        File.Delete(DescriptorPath);
                    }
                }
            }
            catch (Exception)
            {
            }
        }

        public static void SetStatus(string message)
        {
            try
            {
                UFSession.GetUFSession().Ui.SetStatus(message);
            }
            catch (Exception)
            {
            }
        }

        private static void Show(NXMessageBox.DialogType type, string message)
        {
            try
            {
                UI.GetUI().NXMessageBox.Show("NX MCP", type, message);
            }
            catch (Exception)
            {
            }
        }

        public static void Log(string message)
        {
            try
            {
                lock (LogLock)
                {
                    CreateProtectedDirectory(stateDirectory);
                    File.AppendAllText(
                        LogPath,
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture) + " " + message +
                        Environment.NewLine);
                }
            }
            catch (Exception)
            {
            }
        }
    }
}
