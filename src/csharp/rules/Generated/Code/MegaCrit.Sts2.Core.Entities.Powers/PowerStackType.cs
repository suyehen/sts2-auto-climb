// Generated migration unit; build and behavior verification are separate.
namespace AutoSlay.Rules.Native.Entities.Powers;

public enum PowerStackType
{
	None,
	/// <summary>
	/// Amount is visible, and must be manually incremented/decremented via an action.
	/// </summary>
	Counter,
	/// <summary>
	/// Amount is hidden, and is always 1.
	/// </summary>
	Single
}
