// Test doubles surround the verbatim production BridgeHost/Json/Protocol code.
// Compile with the in-box C# 5 compiler; no NX installation or packages required.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;

internal static class Harness
{
    internal static string Mode;
    internal static int Started, ListenerStopped, DispatcherStopped, Disposed, Port;
    internal static FileStream Blocker;

    internal static object Invoke(string name, params object[] args)
    {
        try
        {
            return typeof(NxMcp.GuiBridge.BridgeHost).GetMethod(
                name, BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, args);
        }
        catch (TargetInvocationException error) { throw error.InnerException; }
    }

    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Mode = args[0];
        string state = args[1];
        Environment.SetEnvironmentVariable("NX_MCP_STATE_DIR", state);
        Environment.SetEnvironmentVariable("NX_MCP_WORKSPACE", Path.Combine(state, "workspace"));
        typeof(NxMcp.GuiBridge.BridgeHost).GetField(
            "stateDirectory", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, state);
        try
        {
            if (Mode == "lock")
            {
                Invoke("CreateProtectedDirectory", state);
                using ((IDisposable)Invoke("AcquireDescriptorStartupLock"))
                {
                    Console.WriteLine("LOCKED");
                    Console.ReadLine();
                }
                return 0;
            }
            if (Mode == "writers")
            {
                var paths = new List<string>();
                using (var watcher = new FileSystemWatcher(state, "bridge.json.tmp*"))
                {
                    watcher.Created += delegate(object sender, FileSystemEventArgs e)
                    {
                        lock (paths) { paths.Add(e.Name); }
                    };
                    watcher.EnableRaisingEvents = true;
                    for (int i = 0; i < 10; i++)
                    {
                        try { Invoke("WriteDescriptor", 1, "test-only", "test"); }
                        catch (IOException) { }
                    }
                    SpinWait.SpinUntil(delegate { lock (paths) { return paths.Count >= 10; } }, 3000);
                }
                lock (paths) { Console.WriteLine(NxMcp.GuiBridge.Json.Serialize(paths)); }
                return 0;
            }
            if (Mode == "remove")
            {
                typeof(NxMcp.GuiBridge.BridgeHost).GetField(
                    "token", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, "previous");
                Console.WriteLine("ENTER");
                Invoke("RemoveDescriptor");
                Console.WriteLine("REMOVED_OR_PRESERVED");
                return 0;
            }
            if (Mode == "acl")
            {
                foreach (string file in new[] { "bridge.lock", "bridge.json" })
                {
                    FileSecurity security = File.GetAccessControl(Path.Combine(state, file));
                    if (!security.AreAccessRulesProtected) { throw new Exception("Inherited file ACL"); }
                    foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
                    {
                        if (rule.AccessControlType == AccessControlType.Allow &&
                            !rule.IdentityReference.Equals(WindowsIdentity.GetCurrent().User))
                        { throw new Exception("File accessible to another account"); }
                    }
                }
                Console.WriteLine("USER_ONLY");
                return 0;
            }
            Console.WriteLine("ENTER");
            Invoke("Start");
            Console.WriteLine("STARTED");
            Console.ReadLine();
            NxMcp.GuiBridge.BridgeHost.Stop("test");
            Console.WriteLine("STOPPED");
            return 0;
        }
        catch (Exception error)
        {
            if (Blocker != null) { Blocker.Dispose(); }
            Console.WriteLine(NxMcp.GuiBridge.Json.Serialize(new Dictionary<string, object> {
                { "error", error.Message }, { "started", Started },
                { "port", Port },
                { "listener_stopped", ListenerStopped },
                { "dispatcher_stopped", DispatcherStopped }, { "disposed", Disposed }
            }));
            // Keep the failed process alive: tests must prove cleanup, not rely on OS exit.
            Console.ReadLine();
            return 1;
        }
    }
}

namespace System.Windows.Forms
{
    public delegate void MethodInvoker();
    public class Control : IDisposable
    {
        public IntPtr Handle { get { return new IntPtr(1); } }
        public void BeginInvoke(MethodInvoker method) { }
        public void Dispose() { Harness.Disposed++; }
    }
}

namespace NXOpen
{
    public class Session
    {
        public static Session GetSession()
        {
            if (Harness.Mode == "hold" || Harness.Mode == "fail")
            {
                Console.WriteLine("SESSION");
                Console.ReadLine();
            }
            if (Harness.Mode == "fail") { throw new IOException("injected session failure"); }
            return new Session();
        }
        public string GetEnvironmentVariableValue(string name) { return "NX2206-test"; }
    }
    public class NXMessageBox
    {
        public enum DialogType { Information, Error }
        public void Show(string title, DialogType type, string message) { }
    }
    public class UI
    {
        public NXMessageBox NXMessageBox = new NXMessageBox();
        public static UI GetUI() { return new UI(); }
    }
}
namespace NXOpen.UF
{
    public class UFSession
    {
        public UFSession Ui { get { return this; } }
        public static UFSession GetUFSession() { return new UFSession(); }
        public void SetStatus(string message) { }
    }
}
namespace NxMcp.GuiBridge
{
    internal class Workspace
    {
        internal string Root;
        internal Workspace(string root) { Root = root; }
    }
    internal class Executor
    {
        internal Executor(NXOpen.Session session, string version, Workspace workspace) { }
        internal void Execute() { }
    }
    internal class MainThreadDispatcher
    {
        internal MainThreadDispatcher(System.Windows.Forms.Control control, Action call, Action stop) { }
        internal bool Executing { get { return false; } }
        internal void Stop()
        {
            Harness.DispatcherStopped++;
            if (Harness.Mode == "cleanupfail") { throw new IOException("injected cleanup failure"); }
        }
    }
    internal class BridgeServer
    {
        private TcpListener listener;
        private Thread worker;
        internal int Port { get; private set; }
        internal BridgeServer(MainThreadDispatcher dispatcher, string secret, string file, Action stop) { }
        internal void Start()
        {
            listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            Harness.Port = Port;
            Harness.Started++;
            if (Harness.Mode == "listenerfail") { throw new IOException("injected listener failure"); }
            string path = Path.Combine(Environment.GetEnvironmentVariable("NX_MCP_STATE_DIR"), "bridge.json");
            if (Harness.Mode == "writefail" || Harness.Mode == "cleanupfail") { Directory.CreateDirectory(path); }
            if (Harness.Mode == "replacefail")
            {
                Harness.Blocker = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            }
            if (Harness.Mode == "aclfail")
            {
                FileSecurity denied = File.GetAccessControl(path);
                denied.AddAccessRule(new FileSystemAccessRule(
                    WindowsIdentity.GetCurrent().User, FileSystemRights.ChangePermissions, AccessControlType.Deny));
                File.SetAccessControl(path, denied);
            }
            worker = new Thread(delegate()
            {
                try
                {
                    while (true)
                    {
                        using (TcpClient client = listener.AcceptTcpClient())
                        using (var reader = new StreamReader(client.GetStream()))
                        using (var writer = new StreamWriter(client.GetStream()))
                        {
                            reader.ReadLine();
                            writer.WriteLine("{\"ok\":false,\"error\":{\"code\":\"NX_AUTH_FAILED\"}}");
                            writer.Flush();
                        }
                    }
                }
                catch (SocketException) { }
            });
            worker.IsBackground = true;
            worker.Start();
        }
        internal void Stop()
        {
            Harness.ListenerStopped++;
            if (listener != null) { listener.Stop(); }
            if (worker != null && !worker.Join(3000)) { throw new Exception("listener did not stop"); }
        }
        internal void StopAfterResponse() { Stop(); }
    }
}
