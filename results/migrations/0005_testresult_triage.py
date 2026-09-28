from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("results", "0004_elfsubmission")]

    operations = [
        migrations.AddField(
            model_name="testresult",
            name="triage_category",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="testresult",
            name="triage_owner",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="testresult",
            name="triage_explanation",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="testresult",
            name="triage_evidence",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="testresult",
            name="ai_status",
            field=models.CharField(blank=True, max_length=24),
        ),
        migrations.AddField(
            model_name="testresult",
            name="ai_model",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="testresult",
            name="ai_analysis",
            field=models.TextField(blank=True),
        ),
    ]
