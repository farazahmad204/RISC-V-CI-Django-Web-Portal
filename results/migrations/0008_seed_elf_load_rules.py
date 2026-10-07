"""Seed the ELF windows Run ELF accepts per board (see results/elf_check.py).

Each window is where ELFs for that board are linked (ACT config link.ld) and that the
board's runner can load: VF2 below its UART staging buffer at 0x88000000, BPI-F3 above
the runner and below staging at 0x0C200000, Megrez its payload window. The windows do
not overlap, so a wrong-board ELF is rejected. Boards that already have rules keep them.
"""

from django.db import migrations

RULES = {
    "visionfive2": {"window": ["0x80000000", "0x88000000"], "reserved": []},
    "bananapi-f3": {"window": ["0x04200000", "0x0C200000"], "reserved": []},
    "milkv-megrez": {"window": ["0x90000000", "0xB0000000"], "reserved": []},
}


def seed(apps, schema_editor):
    Board = apps.get_model("results", "Board")
    for board in Board.objects.filter(slug__in=RULES):
        profile = board.profile if isinstance(board.profile, dict) else {}
        if "elf_load_rules" not in profile:
            profile["elf_load_rules"] = RULES[board.slug]
            board.profile = profile
            board.save(update_fields=["profile"])


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0007_board_health"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
