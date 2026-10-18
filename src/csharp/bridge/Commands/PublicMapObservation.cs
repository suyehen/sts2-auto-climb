namespace AutoSlayBridge;

// One public topology, scoped to a particular run object, act and generation.
// The caller must strip action authority before storing immutable node records.
public sealed class PublicMapObservation<T>
{
    private object? run;
    private int? act;
    private long? generation;
    private T[] nodes = [];

    public T[] Read(object? currentRun, int? currentAct, long? currentGeneration)
    {
        if (currentRun == null || currentAct == null || currentGeneration == null ||
            !ReferenceEquals(run, currentRun) || act != currentAct || generation != currentGeneration)
        {
            run = currentRun; act = currentAct; generation = currentGeneration; nodes = [];
        }
        return nodes.ToArray();
    }

    public void Observe(object currentRun, int currentAct, long currentGeneration, IEnumerable<T> publicNodes)
    {
        Read(currentRun, currentAct, currentGeneration);
        nodes = publicNodes.ToArray();
    }
}
