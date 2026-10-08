from django.db import migrations


def remove_category_columns(apps, schema_editor):
    # The triage category no longer gets a workbook column; its values go with it.
    apps.get_model("results", "AnalysisColumn").objects.filter(key="triage_category").delete()


class Migration(migrations.Migration):
    dependencies = [("results", "0009_workbook_triage_columns")]

    operations = [migrations.RunPython(remove_category_columns, migrations.RunPython.noop)]
