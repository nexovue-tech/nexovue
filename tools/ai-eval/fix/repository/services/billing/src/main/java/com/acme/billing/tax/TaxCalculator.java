package com.acme.billing.tax;

public final class TaxCalculator {
    private static final long RATE_PERCENT = 20;

    private TaxCalculator() {}

    public static long tax(long netCents) {
        return netCents * RATE_PERCENT;
    }
}
