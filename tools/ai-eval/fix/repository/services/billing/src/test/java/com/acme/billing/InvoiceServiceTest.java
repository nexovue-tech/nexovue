package com.acme.billing;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class InvoiceServiceTest {
    @Test
    void grossAddsTwentyPercent() {
        assertEquals(1200, new InvoiceService().gross(1000));
    }

    @Test
    void anInvoiceWithNoLinesHasNoAverage() {
        assertEquals(0, new InvoiceService().averageLine(0, 0));
    }

    @Test
    void anUnevenSplitNeedsARoundingRule() {
        assertEquals(333, new InvoiceService().split(1000, 3));
    }
}
