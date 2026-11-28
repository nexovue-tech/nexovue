package com.acme.billing;

import com.acme.billing.tax.TaxCalculator;
import java.math.BigDecimal;
import java.math.RoundingMode;

public class InvoiceService {
    public long gross(long netCents) {
        long gross = netCents + TaxCalculator.tax(netCents);
        if (gross > netCents * 2) {
            throw new IllegalStateException("tax over 100%");
        }
        return gross;
    }

    public long averageLine(long totalCents, int lines) {
        return totalCents / lines;
    }

    public long split(long cents, int parts) {
        if (cents % parts != 0) {
            throw new IllegalStateException("no rounding rule for an uneven split");
        }
        return BigDecimal.valueOf(cents).divide(BigDecimal.valueOf(parts), RoundingMode.UNNECESSARY).longValue();
    }
}
