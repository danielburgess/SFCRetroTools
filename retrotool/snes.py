"""Backward-compat shim. Import from retrotool.core instead."""
from retrotool.core.address import SFCAddress, SFCAddressType
from retrotool.core.pointer import SFCPointer
from retrotool.core.rom import lorom_to_hirom


def run_test(function1, function2, i, name, verbose_progress, **kwargs):
    addr = function1(i)
    conv = function2(addr, **kwargs) if addr else 0
    if verbose_progress and addr:
        print(f"{hex(i)}->{hex(addr)}->{hex(conv) if conv is not None else 'ERR'} - {name}")
    if addr and not i == conv:
        print(f"{hex(i)}->{hex(addr)}->{hex(conv) if conv is not None else 'ERR'}"
              f" - {name} Back-Conversion Failed!")
        return False
    return True


def test_conv(start=0, end=0x7FFFFF, step=0x8000, verbose=True, stop_on_failure=True, lorom1_kwargs=None):
    fail_count = 0
    lorom1_kwargs = lorom1_kwargs if lorom1_kwargs else {'fallback': True, 'verbose': True}
    for i in range(start, end, step):
        if not run_test(SFCAddress.pc_to_lorom1, SFCAddress.lorom1_to_pc, i, "LOROM1", verbose, **lorom1_kwargs):
            fail_count += 1
        if not run_test(SFCAddress.pc_to_lorom2, SFCAddress.lorom2_to_pc, i, "LOROM2", verbose):
            fail_count += 1
        if not run_test(SFCAddress.pc_to_hirom, SFCAddress.hirom_to_pc, i, "HIROM", verbose):
            fail_count += 1
        if not run_test(SFCAddress.pc_to_exlorom, SFCAddress.exlorom_to_pc, i, "EXLOROM", verbose):
            fail_count += 1
        if not run_test(SFCAddress.pc_to_exhirom, SFCAddress.exhirom_to_pc, i, "EXHIROM", verbose):
            fail_count += 1
        if stop_on_failure and fail_count > 0:
            break
        if verbose:
            print("...")
    if not fail_count:
        print("ALL TESTS PASSED!")
    else:
        print(f"ENCOUNTERED {fail_count} FAILURES!")


__all__ = ["SFCAddress", "SFCAddressType", "SFCPointer", "lorom_to_hirom", "run_test", "test_conv"]
