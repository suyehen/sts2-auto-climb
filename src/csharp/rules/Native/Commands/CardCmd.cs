using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Nodes.CommonUi;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.State;

namespace AutoSlay.Rules.Native.Commands;

/// <summary>Native upgrade/downgrade rule members. Other card commands and presentation are not exposed.</summary>
public static class CardCmd
{
    public static void Upgrade(CardModel card, CardPreviewStyle style = CardPreviewStyle.HorizontalLayout) => Upgrade(card.World, [card], style);
    /// <summary>
    /// Owned bulk entry takes its world explicitly so Ending can return before enumerating input.
    /// Native source callers of the context-free bulk signature still require migration.
    /// </summary>
    public static void Upgrade(SimulationWorld world, IEnumerable<CardModel> cards, CardPreviewStyle style = CardPreviewStyle.HorizontalLayout)
    {
        world.RequireExecution(); if (world.CombatState?.IsEnding == true) return;
        foreach (var card in cards)
        {
            world.RequireOwnedCard(card);
            if (!card.IsUpgradable) continue;
            card.RequireUpgradeState(); var entry = DeckHistory(card); entry?.RecordCardUpgrade(card.Id, downgrade: false);
            card.UpgradeInternal(); card.FinalizeUpgradeInternal();
            // Native code after this point only chooses local visual containers and upgrade VFX.
        }
    }
    public static void Downgrade(CardModel card)
    {
        var world = card.World; world.RequireExecution(); if (world.CombatState?.IsEnding == true) return;
        world.RequireOwnedCard(card); card.RequireUpgradeState();
        DeckHistory(card)?.RecordCardUpgrade(card.Id, downgrade: true); card.DowngradeInternal();
    }
    private static PlayerMapPointHistoryEntry? DeckHistory(CardModel card)
    {
        if (card.Pile?.Type != PileType.Deck) return null;
        var run = card.Owner.RunState; run.RequireHistory();
        var entry = run.CurrentMapPointHistoryEntry?.GetEntry(card.Owner.NetId); entry?.RequireCardUpgradeHistory(); return entry;
    }
}
