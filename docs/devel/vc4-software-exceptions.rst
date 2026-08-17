VideoCore IV software exceptions
=================================

The VC4 frontend treats both 16-bit SWI encodings as synchronous CPU
exceptions.  The translator records the selector and post-SWI return address,
raises ``VC4_EXCP_SWI``, and leaves vector entry to the CPU exception
dispatcher.

Software exceptions share the architectural exception-frame path with
external IRQs while retaining the production implementation's nesting limit
and per-frame IRQ provenance.  Consequently ``RTI`` completes the interrupt
controller only for an external IRQ frame; returning from SWI cannot
accidentally acknowledge an unrelated interrupt.

The programmed IC0 vector base is queried through a controller accessor
rather than by reaching into device state from the CPU.  Focused immediate-
and register-form tests plus an unchanged official-firmware probe guard the
exception frame, vector selection, scalar entry bit, ``RTI`` restoration, and
the old ``swi 0`` frontier.
