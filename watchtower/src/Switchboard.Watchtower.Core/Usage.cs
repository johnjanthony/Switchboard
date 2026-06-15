namespace Switchboard.Watchtower.Core;

public sealed record Usage(long InputTokens, long CacheCreationTokens, long CacheReadTokens, long OutputTokens)
{
	public long ContextTokens => InputTokens + CacheCreationTokens + CacheReadTokens;
}
