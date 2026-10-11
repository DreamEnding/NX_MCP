// Test doubles surround the verbatim production SavePart/ClosePart handlers.
// No NX installation or native calls are used by this executable.
using System;
using System.Collections.Generic;

namespace NxMcp.GuiBridge
{
    internal sealed partial class Executor
    {
        private readonly Part part = new Part();
        private readonly Workspace workspace = new Workspace();
        private readonly List<int> undoMarks = new List<int> { 1 };
        private readonly Registry objects = new Registry();
        private readonly HashSet<string> unsafeParts = new HashSet<string>();

        private Part WorkPart(bool required) { return part; }
        private static string Name(Part value, string fallback) { return "test"; }
        private static string PartId(Part value) { return "part-id"; }
        private static NxToolError Invalid(string message)
        {
            return new NxToolError("NX_INVALID_ARGUMENT", message);
        }

        private static Dictionary<string, object> Result(params object[] pairs)
        {
            var result = new Dictionary<string, object>();
            for (int i = 0; i < pairs.Length; i += 2)
                result[(string)pairs[i]] = pairs[i + 1];
            return result;
        }

        private static void Check(bool condition, string message)
        {
            if (!condition) throw new Exception(message);
        }

        public static int Main(string[] args)
        {
            Console.OutputEncoding = new System.Text.UTF8Encoding(false);
            try
            {
                var executor = new Executor();
                var status = new PartSaveStatus(int.Parse(args[1]), int.Parse(args[2]));
                executor.part.SaveStatus = status;
                bool discard = args[0] == "discard";
                bool failedSave = !discard &&
                    (status.NumberUnsavedParts > 0 || status.NumberUnsavedObjects > 0);
                NxToolError failure = null;
                var values = new Dictionary<string, object> { { "save", !discard } };
                try
                {
                    if (args[0] == "save") executor.SavePart(values);
                    else executor.ClosePart(values);
                }
                catch (NxToolError error) { failure = error; }

                Check((failure != null) == failedSave, "Incorrect save success/failure result");
                Check(executor.part.SaveCalled == !discard, "Discard-close attempted a save");
                Check(status.Disposed == !discard, "Save status was not disposed correctly");
                bool closed = !failedSave && args[0] != "save";
                Check(executor.part.Closed == closed, "Part closed after a failed save");
                Check(executor.objects.Invalidated == closed, "References invalidated after failure");
                Check(executor.undoMarks.Count == (failedSave ? 1 : 0), "Undo history lost on failure");
                if (failure != null)
                {
                    Check(failure.Code == "NX_API_ERROR", "Incorrect domain error");
                    int nativeCode = status.NumberUnsavedParts > 0 ? 12345 : 67890;
                    Check((int)failure.NxCode == nativeCode, "Native error code was lost");
                    Check(!failure.Retryable, "Failed save was marked retryable");
                }
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error);
                return 1;
            }
        }
    }

    internal class BasePart
    {
        internal enum SaveComponents { False }
        internal enum CloseAfterSave { False }
        internal enum CloseWholeTree { False }
        internal enum CloseModified { CloseModified }
    }

    internal sealed class Part : BasePart
    {
        internal string FullPath = @"C:\workspace\test.prt";
        internal PartSaveStatus SaveStatus;
        internal bool SaveCalled, Closed;

        internal PartSaveStatus Save(SaveComponents components, CloseAfterSave close)
        {
            SaveCalled = true;
            return SaveStatus;
        }

        internal void Close(CloseWholeTree tree, CloseModified modified, object responses)
        {
            Closed = true;
        }
    }

    internal sealed class PartSaveStatus
    {
        internal readonly int NumberUnsavedParts, NumberUnsavedObjects;
        internal bool Disposed;

        internal PartSaveStatus(int parts, int objects)
        {
            NumberUnsavedParts = parts;
            NumberUnsavedObjects = objects;
        }

        internal int GetStatus(int index)
        {
            if (Disposed || index != 0 || NumberUnsavedParts == 0)
                throw new Exception("Invalid part status access");
            return 12345;
        }

        internal int GetObjectStatus(int index)
        {
            if (Disposed || index != 0 || NumberUnsavedObjects == 0)
                throw new Exception("Invalid object status access");
            return 67890;
        }

        internal void Dispose() { Disposed = true; }
    }

    internal sealed class Workspace
    {
        internal void EnsureInside(string path) { }
    }

    internal sealed class Registry
    {
        internal bool Invalidated;
        internal void InvalidatePart(string id) { Invalidated = true; }
    }
}
