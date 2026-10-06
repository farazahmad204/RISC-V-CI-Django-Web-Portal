"""Seed the board info pages for the three CI boards.

Values come from the runner repository's board manifests and specs, the ACT
board configs (riscv-arch-test sifive_u74 / megrez-hypervisor) and probes run
on the boards. A board whose profile was already edited is left unchanged.
"""

from django.db import migrations


def row(label, value, status, source=""):
    return {"label": label, "value": value, "status": status, "source": source}


def extensions(text):
    return [item.split("=") for item in text.split()]


VISIONFIVE2 = {
    "sections": [
        {
            "title": "Identity",
            "rows": [
                row("SoC", "StarFive JH7110", "confirmed", "runner manifest vf2_jh7110.yaml"),
                row(
                    "CPU",
                    "SiFive U74-MC, 4 application harts; CI payloads run on hart 1",
                    "confirmed",
                    "runner manifest vf2_jh7110.yaml",
                ),
            ],
        },
        {
            "title": "ISA",
            "rows": [
                row("Board ISA", "rv64gc", "confirmed", "runner manifest vf2_jh7110.yaml"),
                row(
                    "ISA string tested by ACT",
                    "rv64imafdc_zicntr_zicsr_zifencei_zihpm_zaamo_zalrsc_zca_zcd_zba_zbb"
                    "_sscounterenw_sstvecd_ssu64xl_sv39",
                    "configured",
                    "ACT config sifive_u74/visionfive2-rv64gc.yaml",
                ),
                row(
                    "Privilege modes", "M, S, U", "confirmed", "runner board spec visionfive2.yaml"
                ),
                row(
                    "Virtual memory",
                    "Sv39",
                    "configured",
                    "ACT config sifive_u74/visionfive2-rv64gc.yaml",
                ),
                row("Hypervisor (H)", "Not implemented", "configured", "ACT config (no H)"),
            ],
        },
        {
            "title": "Specification versions",
            "rows": [
                row(
                    "Privileged ISA",
                    "1.11",
                    "configured",
                    "ACT config sifive_u74/visionfive2-rv64gc.yaml (S/Sm 1.11.0)",
                ),
            ],
        },
        {
            "title": "Memory map used by CI",
            "rows": [
                row("Runner load and entry", "0x40000000 (M-mode)", "confirmed", "runner manifest"),
                row("UART staging buffer", "0x88000000", "confirmed", "runner manifest"),
                row(
                    "UART",
                    "16550-compatible at 0x10000000, 32-bit registers, 115200 baud",
                    "confirmed",
                    "runner manifest",
                ),
                row("mtime", "0x0200bff8", "confirmed", "runner manifest"),
            ],
        },
    ],
    "extensions": {
        "source": "ACT config sifive_u74/visionfive2-rv64gc.yaml (UDB implemented_extensions)",
        "items": extensions(
            "I=2.1 M=2.0 Zmmul=1.0.0 A=2.1.0 F=2.2.0 D=2.2.0 C=2.0 U=1.0.0 S=1.11.0 Sm=1.11.0"
            " Zicsr=2.0 Zifencei=2.0.0 Zihpm=2.0.0 Zicntr=2.0 Zaamo=1.0.0 Zalrsc=1.0.0"
            " Zca=1.0.0 Zcd=1.0.0 Zba=1.0.0 Zbb=1.0.0 Sscounterenw=1.0.0 Sstvecd=1.0.0"
            " Ssu64xl=1.0.0 Sv39=1.0.0 Svbare=1.0.0"
        ),
    },
    "boot_flows": [
        {
            "name": "Stock boot",
            "status": "unverified",
            "source": "StarFive default SD boot; CI only replaces the stage after SPL",
            "steps": [
                "Boot ROM",
                "U-Boot SPL (DDR init, SD)",
                "OpenSBI + U-Boot (FIT on SD)",
                "Linux",
            ],
        },
        {
            "name": "CI boot",
            "status": "confirmed",
            "source": "runner manifest vf2_jh7110.yaml; image vf2_act4.its on SD",
            "steps": [
                "Boot ROM",
                "U-Boot SPL (DDR init, SD)",
                "UART ELF runner, M-mode, 0x40000000 (padded U-Boot FIT on SD)",
                "One ELF per power cycle over UART",
            ],
        },
    ],
    "notes": [
        "Boot medium: SD card. Recovery: reflash a known-good VisionFive 2 SD image or the "
        "previously verified runner boot image.",
    ],
}

BANANAPI_F3 = {
    "sections": [
        {
            "title": "Identity",
            "rows": [
                row("SoC", "SpacemiT K1", "confirmed", "runner manifest bpif3_k1.yaml"),
                row(
                    "CPU",
                    "SpacemiT X60, 8 harts; CI payloads run on hart 0",
                    "confirmed",
                    "runner manifest bpif3_k1.yaml",
                ),
                row(
                    "IDs",
                    "mvendorid 0x710, marchid 0x8000000058000001, mimpid 0x1000000049772200",
                    "confirmed",
                    "csr_probe on the board, 2026-10-05",
                ),
            ],
        },
        {
            "title": "ISA",
            "rows": [
                row("Board ISA", "rv64gc", "unverified", "runner manifest bpif3_k1.yaml (partial)"),
                row(
                    "ISA string tested by ACT",
                    "rv64imafdcbv_zfh_zfhmin_zba_zbb_zbc_zbs_zbkc_zkt_zca_zcd_zicbom_zicboz"
                    "_zicntr_zicond_zicsr_zifencei_zihintpause_zihpm_zaamo_zalrsc_sscofpmf_sstc"
                    "_sv39_svinval_svnapot_svpbmt_zvfh_zvfhmin_zvkt_zvl256b",
                    "configured",
                    "ACT config bpif3/bpif3-rva22s64.yaml (RVA22S64 profile + V)",
                ),
                row("Privilege modes", "M, S, U", "configured", "ACT config bpif3-rva22s64.yaml"),
                row("Virtual memory", "Sv39", "configured", "ACT config bpif3-rva22s64.yaml"),
                row(
                    "Vector",
                    "V 1.0, VLEN 256 (Zvl256b)",
                    "configured",
                    "ACT config bpif3-rva22s64.yaml",
                ),
                row(
                    "Hypervisor (H)",
                    "Not documented for this board",
                    "unknown",
                    "runner manifest bpif3_k1.yaml (isa_h_documented: false)",
                ),
            ],
        },
        {
            "title": "Specification versions",
            "rows": [
                row(
                    "Privileged ISA (vendor claim)",
                    "Unknown",
                    "unknown",
                    "No vendor statement recorded yet",
                ),
                row(
                    "Privileged ISA (tested)",
                    "1.13",
                    "configured",
                    "ACT config bpif3-rva22s64.yaml (S/Sm 1.13.0)",
                ),
            ],
        },
        {
            "title": "Memory map used by CI",
            "rows": [
                row("Runner load and entry", "0x04000000 (M-mode)", "confirmed", "runner manifest"),
                row("ACT ELF payloads", "from 0x04200000", "confirmed", "ACT config link.ld"),
                row("UART staging buffer", "0x0C200000", "confirmed", "runner manifest"),
                row(
                    "UART",
                    "16550-compatible at 0xD4017000, 8-bit registers, 115200 baud",
                    "confirmed",
                    "runner manifest",
                ),
                row("CLINT", "0xE4000000 (mtime 0xE400BFF8)", "confirmed", "runner manifest"),
                row(
                    "Access faults",
                    "None for plain addresses: DRAM starts at 0x0 and unmapped space reads back",
                    "confirmed",
                    "csr_probe and device tree k1-x_deb1.dtb, 2026-10-05",
                ),
            ],
        },
    ],
    "extensions": {
        "source": "ACT config bpif3/bpif3-rva22s64.yaml (UDB implemented_extensions)",
        "items": extensions(
            "I=2.1 M=2.0 Zmmul=1.0.0 A=2.1.0 F=2.2.0 D=2.2.0 Zfh=1.0.0 Zfhmin=1.0.0 C=2.0"
            " B=1.0.0 V=1.0.0 U=1.0.0 S=1.13.0 Sm=1.13.0 Zaamo=1.0.0 Zalrsc=1.0.0 Zba=1.0.0"
            " Zbb=1.0.0 Zbc=1.0.0 Zbkc=1.0.0 Zbs=1.0.0 Zkt=1.0.0 Zca=1.0.0 Zcd=1.0.0"
            " Zicbom=1.0.0 Zicboz=1.0.0 Zicntr=2.0 Zicsr=2.0 Zifencei=2.0.0 Zihintpause=2.0.0"
            " Zihpm=2.0.0 Zicond=1.0 Sscofpmf=1.0.0 Sstc=1.0.0 Sv39=1.0.0 Svbare=1.0.0"
            " Svpbmt=1.0.0 Svinval=1.0.0 Svnapot=1.0.0 Zve32x=1.0.0 Zve32f=1.0.0 Zve64x=1.0.0"
            " Zve64f=1.0.0 Zve64d=1.0.0 Zvfh=1.0.0 Zvfhmin=1.0.0 Zvkt=1.0.0 Zvl32b=1.0.0"
            " Zvl64b=1.0.0 Zvl128b=1.0.0 Zvl256b=1.0.0"
        ),
    },
    "boot_flows": [
        {
            "name": "Stock boot",
            "status": "unverified",
            "source": "Bianbu/Armbian SD image; Linux boot validated on the board",
            "steps": [
                "Boot ROM",
                "U-Boot SPL (DDR init, SD)",
                "OpenSBI (GPT partition 3)",
                "U-Boot",
                "Linux",
            ],
        },
        {
            "name": "CI boot",
            "status": "confirmed",
            "source": "runner manifest bpif3_k1.yaml; FIT bpif3_opensbi.its in GPT partition 3",
            "steps": [
                "Boot ROM",
                "U-Boot SPL (DDR init, SD)",
                "UART ELF runner, M-mode, 0x04000000 (FIT in the 'opensbi' partition)",
                "One ELF per power cycle over UART",
            ],
        },
    ],
    "notes": [
        "The 'opensbi' partition is only the boot slot: the runner contains no OpenSBI.",
        "Boot medium: SD card. Recovery: restore a known-good BPI-F3 SD image or the previously "
        "verified OpenSBI FIT in GPT partition 3.",
    ],
}

MILKV_MEGREZ = {
    "sections": [
        {
            "title": "Identity",
            "rows": [
                row(
                    "SoC",
                    "ESWIN EIC7700X (board revision V1.1)",
                    "confirmed",
                    "runner manifest milkv_megrez.yaml",
                ),
                row(
                    "CPU",
                    "SiFive P550, 4 harts; CI payloads run on hart 1",
                    "confirmed",
                    "runner manifest milkv_megrez.yaml",
                ),
                row(
                    "IDs",
                    "mvendorid 0x489, marchid 0x8000000000000008, mimpid 0x6220425",
                    "confirmed",
                    "csr_probe on the board, 2026-10-05",
                ),
            ],
        },
        {
            "title": "ISA",
            "rows": [
                row(
                    "Board ISA",
                    "rv64imafdchx",
                    "confirmed",
                    "OpenSBI v1.5 boot log on the board (Boot HART Base ISA)",
                ),
                row(
                    "ISA string tested by ACT",
                    "rv64imafdc_zicntr_zicsr_zifencei_zihpm_zaamo_zalrsc_zca_zcd_zba_zbb"
                    "_sscofpmf_sscounterenw_sstvecd_ssu64xl_sv39_sv48",
                    "configured",
                    "ACT config milkv_megrez/milkv-megrez-p550.yaml (sifive_u74)",
                ),
                row(
                    "Privilege modes",
                    "M, S, U; HS, VS, VU with H",
                    "confirmed",
                    "EIC7700X TRM v1.0.0-20250103 Part 1, chapter 3.4",
                ),
                row(
                    "Virtual memory",
                    "Sv39, Sv48; G-stage Sv39x4, Sv48x4",
                    "confirmed",
                    "EIC7700X TRM satp/hgatp tables; device tree mmu-type",
                ),
            ],
        },
        {
            "title": "Specification versions",
            "rows": [
                row(
                    "Privileged ISA",
                    "1.11",
                    "confirmed",
                    "EIC7700X TRM Table 3-5; OpenSBI 'Boot HART Priv Version v1.11'",
                ),
                row("Hypervisor (H)", "Draft 0.6", "confirmed", "EIC7700X TRM Table 3-5"),
                row(
                    "Hypervisor tests",
                    "Run against ratified H 1.0 with S/Sm declared 1.12, as UDB requires",
                    "deviation",
                    "ACT branch megrez-hypervisor, config README",
                ),
            ],
        },
        {
            "title": "Memory map used by CI",
            "rows": [
                row("Runner load and entry", "0x80000000 (M-mode)", "confirmed", "runner manifest"),
                row("ACT ELF payloads", "0x90000000 to 0xB0000000", "confirmed", "runner manifest"),
                row("UART staging buffer", "0xC0000000", "confirmed", "runner manifest"),
                row(
                    "UART",
                    "8250 UART0 at 0x50900000, 32-bit registers, 115200 baud;"
                    " runner initialises it",
                    "confirmed",
                    "runner manifest",
                ),
                row("Timer", "mtime 1 MHz", "confirmed", "runner manifest"),
                row(
                    "Boot time",
                    "About 157 s from power-on to READY",
                    "confirmed",
                    "CI runs, 2026-10",
                ),
            ],
        },
    ],
    "extensions": {
        "source": "ACT config milkv-megrez-p550.yaml (sifive_u74); H 1.0 only on megrez-hypervisor",
        "items": extensions(
            "I=2.1 M=2.0 Zmmul=1.0.0 A=2.1.0 F=2.2.0 D=2.2.0 C=2.0 U=1.0.0 S=1.11.0 Sm=1.11.0"
            " Zicsr=2.0 Zifencei=2.0.0 Zihpm=2.0.0 Zicntr=2.0 Zaamo=1.0.0 Zalrsc=1.0.0"
            " Zca=1.0.0 Zcd=1.0.0 Zba=1.0.0 Zbb=1.0.0 Sscofpmf=1.0.0 Sscounterenw=1.0.0"
            " Sstvecd=1.0.0 Ssu64xl=1.0.0 Sv39=1.0.0 Sv48=1.0.0 Svbare=1.0.0"
        ),
    },
    "boot_flows": [
        {
            "name": "Stock boot",
            "status": "confirmed",
            "source": "runner manifest milkv_megrez.yaml (normal_chain)",
            "steps": [
                "ESWIN ROM / SCPU",
                "SPI boot chain",
                "sys_init",
                "DDR firmware",
                "OpenSBI (M-mode)",
                "U-Boot (S-mode)",
                "RockOS from SD",
            ],
        },
        {
            "name": "CI boot",
            "status": "confirmed",
            "source": "runner manifest milkv_megrez.yaml (runner_chain);"
            " installed in SPI flash 2026-10-03",
            "steps": [
                "ESWIN ROM / SCPU",
                "SPI boot chain",
                "sys_init",
                "DDR firmware",
                "UART ELF runner, M-mode, 0x80000000 (ESWIN nsign image replacing fw_payload.bin)",
                "One ELF per power cycle over UART",
            ],
        },
    ],
    "notes": [
        "Boot medium: SPI NOR boot chain; the stock OS is on SD. With the runner installed, "
        "RockOS no longer boots.",
        "CI uses an external USB-TTL adapter on UART0; the USB-C debug port must be unplugged "
        "(its onboard CH340 drives UART0 RX).",
        "Recovery: hold Recovery while powering on, connect USB-C until the ESWIN-2030 drive "
        "appears, copy the official Megrez bootloader to start U-Boot, then restore a known-good "
        "bootloader to SPI with es_burn.",
    ],
}

PROFILES = {
    "visionfive2": VISIONFIVE2,
    "bananapi-f3": BANANAPI_F3,
    "milkv-megrez": MILKV_MEGREZ,
}


def seed(apps, schema_editor):
    Board = apps.get_model("results", "Board")
    for board in Board.objects.filter(slug__in=PROFILES):
        if not board.profile:
            board.profile = PROFILES[board.slug]
            board.save(update_fields=["profile"])


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0005_board_profile"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
