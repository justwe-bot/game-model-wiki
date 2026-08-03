using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

internal static class Program
{
    public static int Main(string[] args)
    {
        if (args.Length < 5)
        {
            Console.Error.WriteLine("Usage: Hades2GrannyRebuild <granny2_x64.dll> <source.sdb> <source.gpk> <entry-name> <output.gr2>");
            return 2;
        }

        string dllPath = Path.GetFullPath(args[0]);
        string sdbPath = Path.GetFullPath(args[1]);
        string gpkPath = Path.GetFullPath(args[2]);
        string entryName = args[3];
        string outputPath = Path.GetFullPath(args[4]);

        using (GrannyApi granny = new GrannyApi(dllPath))
        {
            string logPath = outputPath + ".log";
            granny.SetLogFileName(logPath, true);

            List<GpkEntry> entries = GpkReader.Read(gpkPath);
            GpkEntry entry = entries.SingleOrDefault(candidate => candidate.Name == entryName);
            if (entry == null)
            {
                Console.Error.WriteLine("Entry not found: " + entryName);
                Console.Error.WriteLine(string.Join(Environment.NewLine, entries.Select(candidate => candidate.Name).ToArray()));
                return 3;
            }

            Console.WriteLine("Entry: " + entry.Name);
            Console.WriteLine("Compressed payload: " + entry.Payload.Length.ToString("N0") + " bytes");
            byte[] rawGr2 = Lz4Block.Decode(entry.Payload);
            Console.WriteLine("Expanded GR2: " + rawGr2.Length.ToString("N0") + " bytes");
            Console.WriteLine("CRC valid: " + granny.FileCrcIsValid(rawGr2));

            IntPtr sdbFile = granny.ReadEntireFile(sdbPath);
            if (sdbFile == IntPtr.Zero)
            {
                Console.Error.WriteLine("GrannyReadEntireFile failed for the SDB.");
                return 4;
            }

            try
            {
                IntPtr stringDatabase = granny.GetStringDatabase(sdbFile);
                if (stringDatabase == IntPtr.Zero)
                {
                    Console.Error.WriteLine("GrannyGetStringDatabase returned null.");
                    return 5;
                }

                IntPtr packedFile = granny.ReadEntireFileFromMemory(rawGr2);
                if (packedFile == IntPtr.Zero)
                {
                    Console.Error.WriteLine("GrannyReadEntireFileFromMemory failed for the GPK block.");
                    return 6;
                }

                try
                {
                    if (!granny.RemapFileStrings(packedFile, stringDatabase))
                    {
                        Console.Error.WriteLine("GrannyRemapFileStrings failed.");
                        return 7;
                    }

                    granny.PrintFileInfo("Remapped source", packedFile);

                    GrannyFile file = Marshal.PtrToStructure<GrannyFile>(packedFile);
                    if (file.SourceMagicValue == IntPtr.Zero || file.SectionCount <= 0)
                    {
                        Console.Error.WriteLine("The packed Granny file has invalid container metadata.");
                        return 8;
                    }

                    Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
                    Variant root = granny.GetDataTreeFromFile(packedFile);
                    if (root.Type == IntPtr.Zero || root.Object == IntPtr.Zero)
                    {
                        Console.Error.WriteLine("GrannyGetDataTreeFromFile returned an empty root.");
                        return 9;
                    }

                    int typeSection = granny.GetFileSectionOfLoadedObject(packedFile, root.Type);
                    int objectSection = granny.GetFileSectionOfLoadedObject(packedFile, root.Object);
                    uint fileTypeTag = granny.GetFileTypeTag(packedFile);
                    Console.WriteLine("File type tag: 0x" + fileTypeTag.ToString("X8"));
                    Console.WriteLine("Sections: " + file.SectionCount + "; root type/object: " + typeSection + "/" + objectSection);

                    IntPtr writer = granny.BeginFileDataTreeWriting(root.Type, root.Object, typeSection, objectSection);
                    if (writer == IntPtr.Zero)
                    {
                        Console.Error.WriteLine("GrannyBeginFileDataTreeWriting failed.");
                        return 10;
                    }

                    try
                    {
                        granny.PreserveObjectFileSections(writer, packedFile);
                        if (!granny.WriteDataTreeToFile(writer, fileTypeTag, file.SourceMagicValue, outputPath, file.SectionCount))
                        {
                            Console.Error.WriteLine("GrannyWriteDataTreeToFile failed.");
                            return 11;
                        }

                        IntPtr rebuiltFile = granny.ReadEntireFile(outputPath);
                        if (rebuiltFile != IntPtr.Zero)
                        {
                            try { granny.PrintFileInfo("Rebuilt output", rebuiltFile); }
                            finally { granny.FreeFile(rebuiltFile); }
                        }
                    }
                    finally
                    {
                        granny.EndFileDataTreeWriting(writer);
                    }
                }
                finally
                {
                    granny.FreeFile(packedFile);
                }
            }
            finally
            {
                granny.FreeFile(sdbFile);
            }

            Console.WriteLine("Wrote: " + outputPath);
            return 0;
        }
    }
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct Variant
{
    public IntPtr Type;
    public IntPtr Object;
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct GrannyFile
{
    public int IsByteReversed;
    public IntPtr Header;
    public IntPtr SourceMagicValue;
    public int SectionCount;
    public IntPtr Sections;
    public IntPtr Marshalled;
    public IntPtr IsUserMemory;
    public IntPtr ConversionBuffer;
    public UIntPtr ConversionBufferSize;
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct GrannyFileInfo
{
    public IntPtr ArtToolInfo;
    public IntPtr ExporterInfo;
    public IntPtr FromFileName;
    public int TextureCount;
    public IntPtr Textures;
    public int MaterialCount;
    public IntPtr Materials;
    public int SkeletonCount;
    public IntPtr Skeletons;
    public int VertexDataCount;
    public IntPtr VertexDatas;
    public int TriTopologyCount;
    public IntPtr TriTopologies;
    public int MeshCount;
    public IntPtr Meshes;
    public int ModelCount;
    public IntPtr Models;
    public int TrackGroupCount;
    public IntPtr TrackGroups;
    public int AnimationCount;
    public IntPtr Animations;
    public Variant ExtendedData;
}

internal sealed class GpkEntry
{
    public readonly string Name;
    public readonly byte[] Payload;

    public GpkEntry(string name, byte[] payload)
    {
        Name = name;
        Payload = payload;
    }
}

internal static class GpkReader
{
    private static readonly byte[] Magic = Hex("E59B495E6F631F141E13EBA990BEEDC4");

    public static List<GpkEntry> Read(string path)
    {
        byte[] data = File.ReadAllBytes(path);
        if (data.Length < 8 || BitConverter.ToUInt32(data, 0) != 1)
        {
            throw new InvalidDataException("Unsupported GPK: " + path);
        }

        uint declaredCount = BitConverter.ToUInt32(data, 4);
        List<GpkEntry> result = new List<GpkEntry>((int)declaredCount);
        int cursor = 8;
        for (int index = 0; index < declaredCount; index++)
        {
            if (cursor >= data.Length)
            {
                throw new InvalidDataException("Unexpected end of GPK entry table.");
            }

            int nameLength = data[cursor++];
            if (cursor + nameLength + 4 > data.Length || !IsAscii(data, cursor, nameLength))
            {
                throw new InvalidDataException("Invalid GPK entry name at offset " + (cursor - 1) + ".");
            }

            string name = Encoding.ASCII.GetString(data, cursor, nameLength);
            cursor += nameLength;
            uint payloadLength = BitConverter.ToUInt32(data, cursor);
            cursor += 4;
            if (payloadLength > int.MaxValue || cursor + (long)payloadLength > data.Length)
            {
                throw new InvalidDataException("Invalid payload length for " + name + ".");
            }

            byte[] payload = new byte[(int)payloadLength];
            Buffer.BlockCopy(data, cursor, payload, 0, payload.Length);
            cursor += payload.Length;
            result.Add(new GpkEntry(name, payload));
        }

        if (cursor != data.Length) throw new InvalidDataException("Trailing bytes after final GPK entry: " + (data.Length - cursor));
        return result;
    }

    private static bool IsAscii(byte[] data, int start, int count)
    {
        for (int index = 0; index < count; index++)
        {
            byte value = data[start + index];
            if (value < 32 || value > 126) return false;
        }
        return true;
    }

    private static byte[] Hex(string value)
    {
        byte[] bytes = new byte[value.Length / 2];
        for (int index = 0; index < bytes.Length; index++)
        {
            bytes[index] = Convert.ToByte(value.Substring(index * 2, 2), 16);
        }
        return bytes;
    }

}

internal static class Lz4Block
{
    public static byte[] Decode(byte[] input)
    {
        List<byte> output = new List<byte>(input.Length * 3);
        int cursor = 0;
        while (cursor < input.Length)
        {
            byte token = input[cursor++];
            int literalLength = ReadLength(input, ref cursor, token >> 4);
            if (cursor + literalLength > input.Length) throw new InvalidDataException("LZ4 literal overruns input.");
            for (int index = 0; index < literalLength; index++) output.Add(input[cursor++]);
            if (cursor == input.Length) break;
            if (cursor + 2 > input.Length) throw new InvalidDataException("LZ4 match offset is truncated.");

            int offset = input[cursor] | (input[cursor + 1] << 8);
            cursor += 2;
            if (offset <= 0 || offset > output.Count) throw new InvalidDataException("Invalid LZ4 match offset: " + offset);

            int matchLength = ReadLength(input, ref cursor, token & 0x0F) + 4;
            for (int index = 0; index < matchLength; index++)
            {
                output.Add(output[output.Count - offset]);
            }
        }

        byte[] result = output.ToArray();
        if (result.Length < MagicLength || !StartsWithGrannyMagic(result))
        {
            throw new InvalidDataException("Expanded block is not a 64-bit little-endian Granny file.");
        }
        return result;
    }

    private const int MagicLength = 16;
    private static readonly byte[] GrannyMagic = new byte[] { 0xE5, 0x9B, 0x49, 0x5E, 0x6F, 0x63, 0x1F, 0x14, 0x1E, 0x13, 0xEB, 0xA9, 0x90, 0xBE, 0xED, 0xC4 };

    private static int ReadLength(byte[] input, ref int cursor, int initial)
    {
        int length = initial;
        if (initial != 15) return length;
        byte extension;
        do
        {
            if (cursor >= input.Length) throw new InvalidDataException("Truncated LZ4 length.");
            extension = input[cursor++];
            length += extension;
        } while (extension == 255);
        return length;
    }

    private static bool StartsWithGrannyMagic(byte[] data)
    {
        for (int index = 0; index < GrannyMagic.Length; index++)
        {
            if (data[index] != GrannyMagic[index]) return false;
        }
        return true;
    }
}

internal sealed class GrannyApi : IDisposable
{
    private readonly IntPtr library;
    private readonly ReadEntireFileDelegate readEntireFile;
    private readonly ReadEntireFileFromMemoryDelegate readEntireFileFromMemory;
    private readonly FileCrcIsValidFromMemoryDelegate fileCrcIsValidFromMemory;
    private readonly GetStringDatabaseDelegate getStringDatabase;
    private readonly RemapFileStringsDelegate remapFileStrings;
    private readonly GetFileInfoDelegate getFileInfo;
    private readonly GetDataTreeFromFileDelegate getDataTreeFromFile;
    private readonly GetFileTypeTagDelegate getFileTypeTag;
    private readonly GetFileSectionOfLoadedObjectDelegate getFileSectionOfLoadedObject;
    private readonly BeginFileDataTreeWritingDelegate beginFileDataTreeWriting;
    private readonly PreserveObjectFileSectionsDelegate preserveObjectFileSections;
    private readonly WriteDataTreeToFileDelegate writeDataTreeToFile;
    private readonly EndFileDataTreeWritingDelegate endFileDataTreeWriting;
    private readonly SetLogFileNameDelegate setLogFileName;
    private readonly FreeFileDelegate freeFile;

    public GrannyApi(string dllPath)
    {
        library = LoadLibrary(dllPath);
        if (library == IntPtr.Zero)
        {
            throw new InvalidOperationException("LoadLibrary failed: " + dllPath + " (" + Marshal.GetLastWin32Error() + ")");
        }

        readEntireFile = Load<ReadEntireFileDelegate>("GrannyReadEntireFile");
        readEntireFileFromMemory = Load<ReadEntireFileFromMemoryDelegate>("GrannyReadEntireFileFromMemory");
        fileCrcIsValidFromMemory = Load<FileCrcIsValidFromMemoryDelegate>("GrannyFileCRCIsValidFromMemory");
        getStringDatabase = Load<GetStringDatabaseDelegate>("GrannyGetStringDatabase");
        remapFileStrings = Load<RemapFileStringsDelegate>("GrannyRemapFileStrings");
        getFileInfo = Load<GetFileInfoDelegate>("GrannyGetFileInfo");
        getDataTreeFromFile = Load<GetDataTreeFromFileDelegate>("GrannyGetDataTreeFromFile");
        getFileTypeTag = Load<GetFileTypeTagDelegate>("GrannyGetFileTypeTag");
        getFileSectionOfLoadedObject = Load<GetFileSectionOfLoadedObjectDelegate>("GrannyGetFileSectionOfLoadedObject");
        beginFileDataTreeWriting = Load<BeginFileDataTreeWritingDelegate>("GrannyBeginFileDataTreeWriting");
        preserveObjectFileSections = Load<PreserveObjectFileSectionsDelegate>("GrannyPreserveObjectFileSections");
        writeDataTreeToFile = Load<WriteDataTreeToFileDelegate>("GrannyWriteDataTreeToFile");
        endFileDataTreeWriting = Load<EndFileDataTreeWritingDelegate>("GrannyEndFileDataTreeWriting");
        setLogFileName = Load<SetLogFileNameDelegate>("GrannySetLogFileName");
        freeFile = Load<FreeFileDelegate>("GrannyFreeFile");
    }

    public IntPtr ReadEntireFile(string path) { return readEntireFile(path); }
    public IntPtr GetStringDatabase(IntPtr file) { return getStringDatabase(file); }
    public bool RemapFileStrings(IntPtr file, IntPtr database) { return remapFileStrings(file, database); }
    public void PrintFileInfo(string label, IntPtr file)
    {
        IntPtr pointer = getFileInfo(file);
        if (pointer == IntPtr.Zero)
        {
            Console.WriteLine(label + ": no file info");
            return;
        }

        GrannyFileInfo info = Marshal.PtrToStructure<GrannyFileInfo>(pointer);
        Console.WriteLine(label + ": meshes=" + info.MeshCount +
            ", models=" + info.ModelCount +
            ", skeletons=" + info.SkeletonCount +
            ", materials=" + info.MaterialCount +
            ", animations=" + info.AnimationCount);
    }
    public Variant GetDataTreeFromFile(IntPtr file)
    {
        Variant result;
        getDataTreeFromFile(file, out result);
        return result;
    }
    public uint GetFileTypeTag(IntPtr file) { return getFileTypeTag(file); }
    public int GetFileSectionOfLoadedObject(IntPtr file, IntPtr value) { return getFileSectionOfLoadedObject(file, value); }
    public IntPtr BeginFileDataTreeWriting(IntPtr type, IntPtr root, int typeSection, int objectSection)
    {
        return beginFileDataTreeWriting(type, root, typeSection, objectSection);
    }
    public void PreserveObjectFileSections(IntPtr writer, IntPtr sourceFile) { preserveObjectFileSections(writer, sourceFile); }
    public bool WriteDataTreeToFile(IntPtr writer, uint fileTypeTag, IntPtr magic, string path, int sectionCount)
    {
        return writeDataTreeToFile(writer, fileTypeTag, magic, path, sectionCount);
    }
    public void EndFileDataTreeWriting(IntPtr writer) { endFileDataTreeWriting(writer); }
    public bool SetLogFileName(string path, bool clear) { return setLogFileName(path, clear); }
    public void FreeFile(IntPtr file) { freeFile(file); }

    public bool FileCrcIsValid(byte[] data)
    {
        GCHandle handle = GCHandle.Alloc(data, GCHandleType.Pinned);
        try { return fileCrcIsValidFromMemory(data.Length, handle.AddrOfPinnedObject()); }
        finally { handle.Free(); }
    }

    public IntPtr ReadEntireFileFromMemory(byte[] data)
    {
        GCHandle handle = GCHandle.Alloc(data, GCHandleType.Pinned);
        try { return readEntireFileFromMemory(data.Length, handle.AddrOfPinnedObject()); }
        finally { handle.Free(); }
    }

    private T Load<T>(string name) where T : class
    {
        IntPtr address = GetProcAddress(library, name);
        if (address == IntPtr.Zero) throw new MissingMethodException(name);
        return (T)(object)Marshal.GetDelegateForFunctionPointer(address, typeof(T));
    }

    public void Dispose()
    {
        if (library != IntPtr.Zero) FreeLibrary(library);
    }

    [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr LoadLibrary(string fileName);

    [DllImport("kernel32", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FreeLibrary(IntPtr module);

    [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Ansi)]
    private static extern IntPtr GetProcAddress(IntPtr module, string name);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    private delegate IntPtr ReadEntireFileDelegate([MarshalAs(UnmanagedType.LPStr)] string fileName);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate IntPtr ReadEntireFileFromMemoryDelegate(int memorySize, IntPtr memory);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool FileCrcIsValidFromMemoryDelegate(int memorySize, IntPtr memory);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate IntPtr GetStringDatabaseDelegate(IntPtr file);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool RemapFileStringsDelegate(IntPtr file, IntPtr stringDatabase);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate IntPtr GetFileInfoDelegate(IntPtr file);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void GetDataTreeFromFileDelegate(IntPtr file, out Variant result);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate uint GetFileTypeTagDelegate(IntPtr file);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetFileSectionOfLoadedObjectDelegate(IntPtr file, IntPtr value);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate IntPtr BeginFileDataTreeWritingDelegate(IntPtr rootObjectTypeDefinition, IntPtr rootObject, int defaultTypeSectionIndex, int defaultObjectSectionIndex);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void PreserveObjectFileSectionsDelegate(IntPtr writer, IntPtr sourceFile);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool WriteDataTreeToFileDelegate(IntPtr writer, uint fileTypeTag, IntPtr platformMagicValue, [MarshalAs(UnmanagedType.LPStr)] string fileName, int fileSectionCount);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void EndFileDataTreeWritingDelegate(IntPtr writer);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl, CharSet = CharSet.Ansi)]
    [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool SetLogFileNameDelegate([MarshalAs(UnmanagedType.LPStr)] string fileName, [MarshalAs(UnmanagedType.I1)] bool clear);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void FreeFileDelegate(IntPtr file);
}
