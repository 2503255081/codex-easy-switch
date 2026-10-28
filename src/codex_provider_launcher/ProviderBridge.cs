using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Runtime.InteropServices;

public class ProviderBridge {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern IntPtr GetEnvironmentStringsW();
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool FreeEnvironmentStringsW(IntPtr block);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool SetEnvironmentVariableW(string name,string value);
    static void NormalizePath() {
        var names=new List<string>();var values=new List<string>();IntPtr block=GetEnvironmentStringsW();
        if(block==IntPtr.Zero)throw new Exception("Cannot inspect process environment.");
        try {
            IntPtr ptr=block;
            while(true) {
                string entry=Marshal.PtrToStringUni(ptr);if(string.IsNullOrEmpty(entry))break;
                int eq=entry.IndexOf('=');
                if(eq>0 && string.Equals(entry.Substring(0,eq),"PATH",StringComparison.OrdinalIgnoreCase)) {names.Add(entry.Substring(0,eq));values.Add(entry.Substring(eq+1));}
                ptr=IntPtr.Add(ptr,(entry.Length+1)*2);
            }
        } finally {FreeEnvironmentStringsW(block);}
        if(names.Count>1) {
            foreach(string name in names)SetEnvironmentVariableW(name,null);
            string merged=string.Join(";",values.SelectMany(v=>v.Split(';')).Where(v=>v.Length>0).Distinct(StringComparer.OrdinalIgnoreCase));
            if(!SetEnvironmentVariableW("Path",merged))throw new Exception("Cannot normalize process PATH.");
        }
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attr, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int info, IntPtr data, uint length);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits { public long a,b; public uint flags; public UIntPtr c,d; public uint e; public UIntPtr f; public uint g,h; }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong a,b,c,d,e,f; }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits { public BasicLimits basic; public IoCounters io; public UIntPtr a,b,c,d; }
    static IntPtr MakeJob(Process child) {
        IntPtr job=CreateJobObject(IntPtr.Zero,null);
        var limits=new ExtendedLimits(); limits.basic.flags=0x2000;
        int size=Marshal.SizeOf(limits); IntPtr ptr=Marshal.AllocHGlobal(size);
        try { Marshal.StructureToPtr(limits,ptr,false);
            if(job==IntPtr.Zero || !SetInformationJobObject(job,9,ptr,(uint)size) || !AssignProcessToJobObject(job,child.Handle)) {
                if(job!=IntPtr.Zero)CloseHandle(job); throw new Exception("Cannot establish child process cleanup job.");
            }
            return job;
        } finally { Marshal.FreeHGlobal(ptr); }
    }
    public static string Quote(string s) {
        var b=new StringBuilder("\""); int slashes=0;
        foreach(char c in s) { if(c=='\\'){slashes++;continue;} if(c=='"')b.Append('\\',slashes*2+1);else b.Append('\\',slashes); b.Append(c);slashes=0; }
        b.Append('\\',slashes*2); return b.Append('"').ToString();
    }
    public static string Rewrite(string line,string provider) {
        if(line.IndexOf("thread/start",StringComparison.Ordinal)<0 && line.IndexOf("thread/resume",StringComparison.Ordinal)<0 && line.IndexOf("thread/fork",StringComparison.Ordinal)<0 && line.IndexOf("turn/start",StringComparison.Ordinal)<0)return line;
        var json=new JavaScriptSerializer {MaxJsonLength=int.MaxValue,RecursionLimit=512};
        var obj=json.DeserializeObject(line) as Dictionary<string,object>;
        if(obj==null || !obj.ContainsKey("method"))return line;
        string method=obj["method"] as string;
        bool thread=method=="thread/start" || method=="thread/resume" || method=="thread/fork";
        bool turn=method=="turn/start";
        if(!thread && !turn)return line;
        string fixedModel=Environment.GetEnvironmentVariable("CODEX_SWITCH_FIXED_MODEL");
        if(!thread && (string.IsNullOrEmpty(fixedModel) || provider!="launcher_custom"))return line;
        if(!string.IsNullOrEmpty(fixedModel) && (provider!="launcher_custom" || fixedModel!="deepseek-flash"))throw new Exception("Invalid fixed model.");
        var p=obj.ContainsKey("params")?obj["params"] as Dictionary<string,object>:null;
        if(p==null){p=new Dictionary<string,object>();obj["params"]=p;}
        if(thread)p["modelProvider"]=provider;
        if(!string.IsNullOrEmpty(fixedModel))p["model"]=fixedModel;
        var config=p.ContainsKey("config")?p["config"] as Dictionary<string,object>:null;
        if(config!=null) {
            if(thread)config["model_provider"]=provider;
            if(!string.IsNullOrEmpty(fixedModel))config["model"]=fixedModel;
        }
        // Record routing decisions only, never prompts or credentials.
        string log=Environment.GetEnvironmentVariable("CODEX_SWITCH_LOG");
        if(!string.IsNullOrEmpty(log))try{File.AppendAllText(log,DateTime.UtcNow.ToString("o")+" "+method+" provider="+provider+Environment.NewLine);}catch{}
        return json.Serialize(obj);
    }
    public static int Main(string[] args) {
        Process child=null;IntPtr job=IntPtr.Zero;
        try {
            NormalizePath();
            string target=Environment.GetEnvironmentVariable("CODEX_SWITCH_REAL_CLI");
            string provider=Environment.GetEnvironmentVariable("CODEX_SWITCH_PROVIDER");
            if(string.IsNullOrEmpty(target)) {
                string manifest=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"real-cli.json");
                if(File.Exists(manifest)) {
                    var info=new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(manifest));
                    string expected;
                    if(!info.TryGetValue("path",out target) || !info.TryGetValue("sha256",out expected) || !Path.IsPathRooted(target) || !File.Exists(target))
                        throw new Exception("Invalid fallback CLI manifest.");
                    using(var stream=File.OpenRead(target)) using(var sha=System.Security.Cryptography.SHA256.Create()) {
                        string actual=BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","");
                        if(!string.Equals(actual,expected,StringComparison.OrdinalIgnoreCase))throw new Exception("Fallback CLI hash mismatch; refresh via Switch-Codex.");
                    }
                }
            }
            if(string.IsNullOrEmpty(target)||!File.Exists(target))throw new Exception("Missing real Codex CLI.");
            if(string.Equals(Path.GetFullPath(target),Path.GetFullPath(Process.GetCurrentProcess().MainModule.FileName),StringComparison.OrdinalIgnoreCase))throw new Exception("Recursive CLI bridge target.");
            bool server=args.Contains("app-server") && !args.Contains("generate-ts") && !args.Contains("generate-json-schema") && !args.Contains("--help");
            if(server && provider!="openai" && provider!="launcher_custom")throw new Exception("Invalid switch provider.");
            var psi=new ProcessStartInfo(target,string.Join(" ",args.Select(Quote))) {UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=new UTF8Encoding(false),StandardErrorEncoding=new UTF8Encoding(false)};
            // Internal child CLI invocations must use the official executable directly.
            psi.EnvironmentVariables["CODEX_CLI_PATH"]=target;
            child=Process.Start(psi);job=MakeJob(child);
            var p=child;
            var output=new Thread(()=>{try{p.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput());}catch{}});
            var error=new Thread(()=>{try{p.StandardError.BaseStream.CopyTo(Console.OpenStandardError());}catch{}});
            var input=new Thread(()=>{try{
                if(server) {using(var reader=new StreamReader(Console.OpenStandardInput(),new UTF8Encoding(false),false,65536))
                    using(var writer=new StreamWriter(p.StandardInput.BaseStream,new UTF8Encoding(false),65536){AutoFlush=true}) {
                        string line;while((line=reader.ReadLine())!=null)writer.WriteLine(Rewrite(line,provider));
                    }
                } else {Console.OpenStandardInput().CopyTo(p.StandardInput.BaseStream);p.StandardInput.Close();}
            }catch(Exception e){Console.Error.WriteLine("Provider bridge input failed: "+e.GetType().Name);try{p.Kill();}catch{}}});
            input.IsBackground=true;output.IsBackground=true;error.IsBackground=true;
            output.Start();error.Start();input.Start();child.WaitForExit();output.Join();error.Join();return child.ExitCode;
        } catch(Exception e){Console.Error.WriteLine("Provider bridge: "+e.Message);if(child!=null)try{child.Kill();}catch{}return 1;}
        finally {if(job!=IntPtr.Zero)CloseHandle(job);if(child!=null)child.Dispose();}
    }
}
