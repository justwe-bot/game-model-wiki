using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;

internal static class Program
{
    private const int FramesPerSecond = 30;

    public static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("Usage: Hades2GrannyExport <granny2_x64.dll> <mesh.gr2> <output.h2gx> [animation.gr2 ...]");
            return 2;
        }

        string dllPath = Path.GetFullPath(args[0]);
        string meshPath = Path.GetFullPath(args[1]);
        string outputPath = Path.GetFullPath(args[2]);
        string[] animationPaths = new string[Math.Max(0, args.Length - 3)];
        for (int index = 0; index < animationPaths.Length; index++) animationPaths[index] = Path.GetFullPath(args[index + 3]);

        using (GrannyApi granny = new GrannyApi(dllPath))
        {
            IntPtr meshFile = granny.ReadEntireFile(meshPath);
            if (meshFile == IntPtr.Zero) throw new InvalidDataException("Unable to read mesh GR2: " + meshPath);

            try
            {
                FileInfoNative info = granny.GetFileInfo(meshFile);
                if (info.ModelCount < 1 || info.MeshCount < 1 || info.SkeletonCount < 1)
                {
                    throw new InvalidDataException("Mesh GR2 is missing a model, mesh, or skeleton.");
                }

                IntPtr modelPointer = ReadPointer(info.Models, 0);
                ModelNative model = Read<ModelNative>(modelPointer);
                SkeletonNative skeleton = Read<SkeletonNative>(model.Skeleton);
                List<AnimationSample> animations = SampleAnimations(granny, modelPointer, model.Skeleton, skeleton.BoneCount, animationPaths);

                string directory = Path.GetDirectoryName(outputPath);
                if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
                using (BinaryWriter writer = new BinaryWriter(File.Create(outputPath)))
                {
                    writer.Write(new byte[] { (byte)'H', (byte)'2', (byte)'G', (byte)'X' });
                    writer.Write(1);
                    WriteSkeleton(writer, skeleton);
                    WriteMeshes(writer, granny, info);
                    WriteAnimations(writer, animations, skeleton.BoneCount);
                }

                Console.WriteLine("Wrote " + outputPath);
                Console.WriteLine("Bones: " + skeleton.BoneCount + "; meshes: " + info.MeshCount + "; animations: " + animations.Count);
            }
            finally
            {
                granny.FreeFile(meshFile);
            }
        }

        return 0;
    }

    private static void WriteSkeleton(BinaryWriter writer, SkeletonNative skeleton)
    {
        writer.Write(skeleton.BoneCount);
        int stride = Marshal.SizeOf(typeof(BoneNative));
        for (int index = 0; index < skeleton.BoneCount; index++)
        {
            BoneNative bone = Read<BoneNative>(IntPtr.Add(skeleton.Bones, index * stride));
            WriteString(writer, ReadString(bone.Name));
            writer.Write(bone.ParentIndex);
            WriteTransform(writer, bone.LocalTransform);
        }
    }

    private static void WriteMeshes(BinaryWriter writer, GrannyApi granny, FileInfoNative info)
    {
        writer.Write(info.MeshCount);
        IntPtr vertexType = granny.GetExportedPointer("GrannyPWNT3432VertexType");
        int vertexStride = Marshal.SizeOf(typeof(VertexNative));
        int groupStride = Marshal.SizeOf(typeof(TriMaterialGroupNative));
        int bindingStride = Marshal.SizeOf(typeof(BoneBindingNative));

        for (int meshIndex = 0; meshIndex < info.MeshCount; meshIndex++)
        {
            IntPtr meshPointer = ReadPointer(info.Meshes, meshIndex);
            MeshNative mesh = Read<MeshNative>(meshPointer);
            WriteString(writer, ReadString(mesh.Name));

            int vertexCount = granny.GetMeshVertexCount(meshPointer);
            writer.Write(vertexCount);
            IntPtr vertices = Marshal.AllocHGlobal(vertexCount * vertexStride);
            try
            {
                granny.CopyMeshVertices(meshPointer, vertexType, vertices);
                for (int index = 0; index < vertexCount; index++)
                {
                    VertexNative vertex = Read<VertexNative>(IntPtr.Add(vertices, index * vertexStride));
                    WriteFloats(writer, vertex.Position);
                    writer.Write(vertex.BoneWeights);
                    writer.Write(vertex.BoneIndices);
                    WriteFloats(writer, vertex.Normal);
                    WriteFloats(writer, vertex.UV);
                }
            }
            finally
            {
                Marshal.FreeHGlobal(vertices);
            }

            int indexCount = granny.GetMeshIndexCount(meshPointer);
            writer.Write(indexCount);
            int[] indices = new int[indexCount];
            GCHandle indexHandle = GCHandle.Alloc(indices, GCHandleType.Pinned);
            try { granny.CopyMeshIndices(meshPointer, 4, indexHandle.AddrOfPinnedObject()); }
            finally { indexHandle.Free(); }
            for (int index = 0; index < indices.Length; index++) writer.Write(indices[index]);

            TriTopologyNative topology = Read<TriTopologyNative>(mesh.PrimaryTopology);
            writer.Write(topology.GroupCount);
            for (int index = 0; index < topology.GroupCount; index++)
            {
                TriMaterialGroupNative group = Read<TriMaterialGroupNative>(IntPtr.Add(topology.Groups, index * groupStride));
                writer.Write(group.MaterialIndex);
                writer.Write(group.TriFirst);
                writer.Write(group.TriCount);
            }

            writer.Write(mesh.BoneBindingCount);
            for (int index = 0; index < mesh.BoneBindingCount; index++)
            {
                BoneBindingNative binding = Read<BoneBindingNative>(IntPtr.Add(mesh.BoneBindings, index * bindingStride));
                WriteString(writer, ReadString(binding.BoneName));
            }

            writer.Write(mesh.MaterialBindingCount);
            for (int index = 0; index < mesh.MaterialBindingCount; index++)
            {
                IntPtr materialPointer = Marshal.ReadIntPtr(mesh.MaterialBindings, index * IntPtr.Size);
                MaterialNative material = Read<MaterialNative>(materialPointer);
                WriteString(writer, ReadString(material.Name));
            }
        }
    }

    private static List<AnimationSample> SampleAnimations(GrannyApi granny, IntPtr model, IntPtr skeleton, int boneCount, string[] paths)
    {
        List<AnimationSample> result = new List<AnimationSample>();
        foreach (string path in paths)
        {
            IntPtr animationFile = granny.ReadEntireFile(path);
            if (animationFile == IntPtr.Zero) throw new InvalidDataException("Unable to read animation GR2: " + path);
            try
            {
                FileInfoNative info = granny.GetFileInfo(animationFile);
                for (int animationIndex = 0; animationIndex < info.AnimationCount; animationIndex++)
                {
                    IntPtr animationPointer = ReadPointer(info.Animations, animationIndex);
                    AnimationNative animation = Read<AnimationNative>(animationPointer);
                    string clipName = Path.GetFileNameWithoutExtension(path);
                    AnimationSample sample = new AnimationSample(clipName, animation.Duration);
                    IntPtr instance = granny.InstantiateModel(model);
                    IntPtr localPose = granny.NewLocalPose(boneCount);
                    try
                    {
                        IntPtr control = granny.PlayControlledAnimation(0.0f, animationPointer, instance);
                        if (control == IntPtr.Zero) throw new InvalidDataException("Unable to bind animation: " + sample.Name);
                        granny.SetControlLoopCount(control, 1);
                        int frameCount = Math.Max(2, (int)Math.Ceiling(animation.Duration * FramesPerSecond) + 1);
                        for (int frame = 0; frame < frameCount; frame++)
                        {
                            float time = Math.Min(animation.Duration, frame / (float)FramesPerSecond);
                            if (frame == frameCount - 1) time = animation.Duration;
                            granny.SetModelClock(instance, time);
                            granny.SampleModelAnimations(instance, 0, boneCount, localPose);
                            TransformNative[] transforms = new TransformNative[boneCount];
                            for (int bone = 0; bone < boneCount; bone++)
                            {
                                transforms[bone] = Read<TransformNative>(granny.GetLocalPoseTransform(localPose, bone));
                            }
                            sample.Frames.Add(new AnimationFrame(time, transforms));
                        }
                    }
                    finally
                    {
                        granny.FreeLocalPose(localPose);
                        granny.FreeModelInstance(instance);
                    }
                    result.Add(sample);
                    Console.WriteLine("Sampled " + sample.Name + " at " + FramesPerSecond + " FPS (" + sample.Frames.Count + " frames)");
                    TransformNative firstRoot = sample.Frames[0].Transforms[0];
                    TransformNative middleRoot = sample.Frames[sample.Frames.Count / 2].Transforms[0];
                    TransformNative lastRoot = sample.Frames[sample.Frames.Count - 1].Transforms[0];
                    Console.WriteLine("  root position first/mid/last: " + Format3(firstRoot.Position) + " / " + Format3(middleRoot.Position) + " / " + Format3(lastRoot.Position));
                }
            }
            finally
            {
                granny.FreeFile(animationFile);
            }
        }
        return result;
    }

    private static void WriteAnimations(BinaryWriter writer, List<AnimationSample> animations, int boneCount)
    {
        writer.Write(animations.Count);
        foreach (AnimationSample animation in animations)
        {
            WriteString(writer, animation.Name);
            writer.Write(animation.Duration);
            writer.Write(animation.Frames.Count);
            foreach (AnimationFrame frame in animation.Frames)
            {
                writer.Write(frame.Time);
                if (frame.Transforms.Length != boneCount) throw new InvalidDataException("Animation bone count mismatch.");
                foreach (TransformNative transform in frame.Transforms) WriteTransform(writer, transform);
            }
        }
    }

    private static void WriteTransform(BinaryWriter writer, TransformNative transform)
    {
        WriteFloats(writer, (transform.Flags & 0x1) != 0 ? transform.Position : IdentityPosition);
        WriteFloats(writer, (transform.Flags & 0x2) != 0 ? transform.Orientation : IdentityOrientation);
        WriteFloats(writer, (transform.Flags & 0x4) != 0 ? transform.ScaleShear : IdentityScaleShear);
    }

    private static readonly float[] IdentityPosition = { 0.0f, 0.0f, 0.0f };
    private static readonly float[] IdentityOrientation = { 0.0f, 0.0f, 0.0f, 1.0f };
    private static readonly float[] IdentityScaleShear = { 1.0f, 0.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 0.0f, 1.0f };

    private static void WriteFloats(BinaryWriter writer, float[] values)
    {
        for (int index = 0; index < values.Length; index++) writer.Write(values[index]);
    }

    private static void WriteString(BinaryWriter writer, string value)
    {
        byte[] bytes = System.Text.Encoding.UTF8.GetBytes(value ?? string.Empty);
        writer.Write(bytes.Length);
        writer.Write(bytes);
    }

    private static string Format3(float[] values)
    {
        return string.Format(CultureInfo.InvariantCulture, "({0:0.###},{1:0.###},{2:0.###})", values[0], values[1], values[2]);
    }

    private static T Read<T>(IntPtr pointer) where T : struct { return Marshal.PtrToStructure<T>(pointer); }
    private static IntPtr ReadPointer(IntPtr array, int index) { return Marshal.ReadIntPtr(array, index * IntPtr.Size); }
    private static string ReadString(IntPtr pointer) { return pointer == IntPtr.Zero ? string.Empty : Marshal.PtrToStringAnsi(pointer); }
}

internal sealed class AnimationSample
{
    public readonly string Name;
    public readonly float Duration;
    public readonly List<AnimationFrame> Frames = new List<AnimationFrame>();
    public AnimationSample(string name, float duration) { Name = name; Duration = duration; }
}

internal sealed class AnimationFrame
{
    public readonly float Time;
    public readonly TransformNative[] Transforms;
    public AnimationFrame(float time, TransformNative[] transforms) { Time = time; Transforms = transforms; }
}

[StructLayout(LayoutKind.Sequential, Pack = 4)] internal struct VariantNative { public IntPtr Type; public IntPtr Object; }
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct TransformNative
{
    public uint Flags;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)] public float[] Position;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public float[] Orientation;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 9)] public float[] ScaleShear;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct FileInfoNative
{
    public IntPtr ArtToolInfo, ExporterInfo, FromFileName;
    public int TextureCount; public IntPtr Textures;
    public int MaterialCount; public IntPtr Materials;
    public int SkeletonCount; public IntPtr Skeletons;
    public int VertexDataCount; public IntPtr VertexDatas;
    public int TriTopologyCount; public IntPtr TriTopologies;
    public int MeshCount; public IntPtr Meshes;
    public int ModelCount; public IntPtr Models;
    public int TrackGroupCount; public IntPtr TrackGroups;
    public int AnimationCount; public IntPtr Animations;
    public VariantNative ExtendedData;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct ModelNative
{
    public IntPtr Name, Skeleton;
    public TransformNative InitialPlacement;
    public int MeshBindingCount;
    public IntPtr MeshBindings;
    public VariantNative ExtendedData;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct SkeletonNative
{
    public IntPtr Name;
    public int BoneCount;
    public IntPtr Bones;
    public int LODType;
    public VariantNative ExtendedData;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct BoneNative
{
    public IntPtr Name;
    public int ParentIndex;
    public TransformNative LocalTransform;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 16)] public float[] InverseWorld4x4;
    public float LODError;
    public VariantNative ExtendedData;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct MeshNative
{
    public IntPtr Name, PrimaryVertexData;
    public int MorphTargetCount; public IntPtr MorphTargets;
    public IntPtr PrimaryTopology;
    public int MaterialBindingCount; public IntPtr MaterialBindings;
    public int BoneBindingCount; public IntPtr BoneBindings;
    public VariantNative ExtendedData;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct VertexNative
{
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)] public float[] Position;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public byte[] BoneWeights;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)] public byte[] BoneIndices;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)] public float[] Normal;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 2)] public float[] UV;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)] internal struct TriMaterialGroupNative { public int MaterialIndex, TriFirst, TriCount; }
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct TriTopologyNative
{
    public int GroupCount; public IntPtr Groups;
    public int IndexCount; public IntPtr Indices;
    public int Index16Count; public IntPtr Indices16;
    public int VertexToVertexCount; public IntPtr VertexToVertexMap;
    public int VertexToTriangleCount; public IntPtr VertexToTriangleMap;
    public int SideToNeighborCount; public IntPtr SideToNeighborMap;
    public int PolygonIndexStartCount; public IntPtr PolygonIndexStarts;
    public int PolygonIndexCount; public IntPtr PolygonIndices;
    public int BonesForTriangleCount; public IntPtr BonesForTriangle;
    public int TriangleToBoneCount; public IntPtr TriangleToBoneIndices;
    public int TriAnnotationSetCount; public IntPtr TriAnnotationSets;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct BoneBindingNative
{
    public IntPtr BoneName;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)] public float[] OBBMin;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)] public float[] OBBMax;
    public int TriangleCount;
    public IntPtr TriangleIndices;
}
[StructLayout(LayoutKind.Sequential, Pack = 4)] internal struct MaterialNative { public IntPtr Name; public int MapCount; public IntPtr Maps, Texture; public VariantNative ExtendedData; }
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct AnimationNative
{
    public IntPtr Name;
    public float Duration, TimeStep, Oversampling;
    public int TrackGroupCount;
    public IntPtr TrackGroups;
    public int DefaultLoopCount, Flags;
    public VariantNative ExtendedData;
}

internal sealed class GrannyApi : IDisposable
{
    private readonly IntPtr library;
    private readonly ReadEntireFileDelegate readEntireFile;
    private readonly GetFileInfoDelegate getFileInfo;
    private readonly FreeFileDelegate freeFile;
    private readonly GetMeshVertexCountDelegate getMeshVertexCount;
    private readonly CopyMeshVerticesDelegate copyMeshVertices;
    private readonly GetMeshIndexCountDelegate getMeshIndexCount;
    private readonly CopyMeshIndicesDelegate copyMeshIndices;
    private readonly InstantiateModelDelegate instantiateModel;
    private readonly FreeModelInstanceDelegate freeModelInstance;
    private readonly PlayControlledAnimationDelegate playControlledAnimation;
    private readonly SetControlLoopCountDelegate setControlLoopCount;
    private readonly SetModelClockDelegate setModelClock;
    private readonly NewLocalPoseDelegate newLocalPose;
    private readonly FreeLocalPoseDelegate freeLocalPose;
    private readonly SampleModelAnimationsDelegate sampleModelAnimations;
    private readonly GetLocalPoseTransformDelegate getLocalPoseTransform;

    public GrannyApi(string dllPath)
    {
        library = LoadLibrary(dllPath);
        if (library == IntPtr.Zero) throw new InvalidOperationException("LoadLibrary failed: " + dllPath);
        readEntireFile = Load<ReadEntireFileDelegate>("GrannyReadEntireFile");
        getFileInfo = Load<GetFileInfoDelegate>("GrannyGetFileInfo");
        freeFile = Load<FreeFileDelegate>("GrannyFreeFile");
        getMeshVertexCount = Load<GetMeshVertexCountDelegate>("GrannyGetMeshVertexCount");
        copyMeshVertices = Load<CopyMeshVerticesDelegate>("GrannyCopyMeshVertices");
        getMeshIndexCount = Load<GetMeshIndexCountDelegate>("GrannyGetMeshIndexCount");
        copyMeshIndices = Load<CopyMeshIndicesDelegate>("GrannyCopyMeshIndices");
        instantiateModel = Load<InstantiateModelDelegate>("GrannyInstantiateModel");
        freeModelInstance = Load<FreeModelInstanceDelegate>("GrannyFreeModelInstance");
        playControlledAnimation = Load<PlayControlledAnimationDelegate>("GrannyPlayControlledAnimation");
        setControlLoopCount = Load<SetControlLoopCountDelegate>("GrannySetControlLoopCount");
        setModelClock = Load<SetModelClockDelegate>("GrannySetModelClock");
        newLocalPose = Load<NewLocalPoseDelegate>("GrannyNewLocalPose");
        freeLocalPose = Load<FreeLocalPoseDelegate>("GrannyFreeLocalPose");
        sampleModelAnimations = Load<SampleModelAnimationsDelegate>("GrannySampleModelAnimations");
        getLocalPoseTransform = Load<GetLocalPoseTransformDelegate>("GrannyGetLocalPoseTransform");
    }

    public IntPtr ReadEntireFile(string path) { return readEntireFile(path); }
    public FileInfoNative GetFileInfo(IntPtr file) { return Marshal.PtrToStructure<FileInfoNative>(getFileInfo(file)); }
    public void FreeFile(IntPtr file) { if (file != IntPtr.Zero) freeFile(file); }
    public int GetMeshVertexCount(IntPtr mesh) { return getMeshVertexCount(mesh); }
    public void CopyMeshVertices(IntPtr mesh, IntPtr type, IntPtr destination) { copyMeshVertices(mesh, type, destination); }
    public int GetMeshIndexCount(IntPtr mesh) { return getMeshIndexCount(mesh); }
    public void CopyMeshIndices(IntPtr mesh, int bytesPerIndex, IntPtr destination) { copyMeshIndices(mesh, bytesPerIndex, destination); }
    public IntPtr InstantiateModel(IntPtr model) { return instantiateModel(model); }
    public void FreeModelInstance(IntPtr instance) { if (instance != IntPtr.Zero) freeModelInstance(instance); }
    public IntPtr PlayControlledAnimation(float startTime, IntPtr animation, IntPtr instance) { return playControlledAnimation(startTime, animation, instance); }
    public void SetControlLoopCount(IntPtr control, int count) { setControlLoopCount(control, count); }
    public void SetModelClock(IntPtr instance, float time) { setModelClock(instance, time); }
    public IntPtr NewLocalPose(int boneCount) { return newLocalPose(boneCount); }
    public void FreeLocalPose(IntPtr pose) { if (pose != IntPtr.Zero) freeLocalPose(pose); }
    public void SampleModelAnimations(IntPtr instance, int firstBone, int boneCount, IntPtr pose) { sampleModelAnimations(instance, firstBone, boneCount, pose); }
    public IntPtr GetLocalPoseTransform(IntPtr pose, int bone) { return getLocalPoseTransform(pose, bone); }
    public IntPtr GetExportedPointer(string name)
    {
        IntPtr address = GetProcAddress(library, name);
        if (address == IntPtr.Zero) throw new MissingMethodException(name);
        IntPtr pointer = Marshal.ReadIntPtr(address);
        if (pointer == IntPtr.Zero) throw new InvalidDataException("Exported pointer is null: " + name);
        return pointer;
    }

    private T Load<T>(string name) where T : class
    {
        IntPtr address = GetProcAddress(library, name);
        if (address == IntPtr.Zero) throw new MissingMethodException(name);
        return (T)(object)Marshal.GetDelegateForFunctionPointer(address, typeof(T));
    }

    public void Dispose() { if (library != IntPtr.Zero) FreeLibrary(library); }

    [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)] private static extern IntPtr LoadLibrary(string fileName);
    [DllImport("kernel32", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool FreeLibrary(IntPtr module);
    [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Ansi)] private static extern IntPtr GetProcAddress(IntPtr module, string name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl, CharSet = CharSet.Ansi)] private delegate IntPtr ReadEntireFileDelegate([MarshalAs(UnmanagedType.LPStr)] string fileName);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr GetFileInfoDelegate(IntPtr file);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void FreeFileDelegate(IntPtr file);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int GetMeshVertexCountDelegate(IntPtr mesh);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void CopyMeshVerticesDelegate(IntPtr mesh, IntPtr vertexType, IntPtr destination);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int GetMeshIndexCountDelegate(IntPtr mesh);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void CopyMeshIndicesDelegate(IntPtr mesh, int bytesPerIndex, IntPtr destination);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr InstantiateModelDelegate(IntPtr model);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void FreeModelInstanceDelegate(IntPtr instance);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr PlayControlledAnimationDelegate(float startTime, IntPtr animation, IntPtr model);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void SetControlLoopCountDelegate(IntPtr control, int count);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void SetModelClockDelegate(IntPtr instance, float time);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr NewLocalPoseDelegate(int boneCount);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void FreeLocalPoseDelegate(IntPtr pose);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void SampleModelAnimationsDelegate(IntPtr instance, int firstBone, int boneCount, IntPtr pose);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr GetLocalPoseTransformDelegate(IntPtr pose, int boneIndex);
}
