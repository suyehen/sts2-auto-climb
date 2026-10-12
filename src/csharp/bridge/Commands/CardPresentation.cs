namespace AutoSlayBridge;

// Tooltip cards are display models that the game may recreate on every read.
// They have content identity only; instance IDs belong to actual actionable cards.
public sealed record CardPresentation(string EntityId, string Name, int UpgradeLevel, string Type,
    string TargetType, int EnergyCost, bool CostsX, string Description);
