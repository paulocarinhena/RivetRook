namespace RivetRook;

public static class AccountBalance
{
    /// <summary>
    /// Returns true only when <paramref name="balance"/> can cover <paramref name="amount"/> without going negative.
    /// </summary>
    public static bool CanWithdraw(decimal balance, decimal amount)
    {
        if (amount <= 0)
            throw new ArgumentOutOfRangeException(nameof(amount));

        return balance + amount >= 0;
    }
}
