// Generated migration unit; build and behavior verification are separate.
namespace AutoSlay.Rules.Native.Entities.Cards;

public static class CardUpgradePreviewTypeExtensions
{
	public static bool IsPreview(this CardUpgradePreviewType previewType)
	{
		return previewType != CardUpgradePreviewType.None;
	}
}
