using System.Reflection;
using System.Runtime.Loader;

var data = args[0];
AssemblyLoadContext.Default.Resolving += (_, name) => {
    var file = Path.Combine(data, name.Name + ".dll");
    return File.Exists(file) ? AssemblyLoadContext.Default.LoadFromAssemblyPath(file) : null;
};
var assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.Combine(data, "sts2.dll"));
var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
var filters = args.Skip(1).ToArray();
foreach (var type in assembly.GetTypes().Where(t => filters.Any(f => t.FullName?.EndsWith(f) == true))) {
    Console.WriteLine("TYPE " + type.FullName);
    if (type.IsEnum) Console.WriteLine("  VALUES " + string.Join(", ", Enum.GetNames(type)));
    foreach (var member in type.GetMembers(flags)) {
        if (member is PropertyInfo p) Console.WriteLine($"  PROP {p.PropertyType.Name} {p.Name} public={p.GetMethod?.IsPublic} static={p.GetMethod?.IsStatic}");
        if (member is FieldInfo f) Console.WriteLine($"  FIELD {f.FieldType.Name} {f.Name} public={f.IsPublic} static={f.IsStatic} literal={f.IsLiteral}" + (f.IsLiteral ? $" value={f.GetRawConstantValue()}" : ""));
        if (member is MethodInfo m && !m.IsSpecialName) Console.WriteLine("  METHOD " + m + " public=" + m.IsPublic);
    }
}
