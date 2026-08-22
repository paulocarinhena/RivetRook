namespace RivetRook;

public static class OrderPricing
{
    public const decimal DiscountThreshold = 100m;
    public const decimal DiscountRate = 0.10m;

    /// <summary>
    /// Returns the payable total. A 10% discount applies when the subtotal is at least 100; otherwise the subtotal is unchanged.
    /// </summary>
    public static decimal FinalPrice(decimal subtotal)
    {
        if (subtotal < 0)
            throw new ArgumentOutOfRangeException(nameof(subtotal));

        if (subtotal < DiscountThreshold)
            return subtotal * (1 - DiscountRate);

        return subtotal;
    }
}
