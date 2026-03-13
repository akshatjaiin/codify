from django.db import models


class DiagnosticSnapshot(models.Model):
	created_at = models.DateTimeField(auto_now_add=True)
	filename = models.CharField(max_length=255, default="untitled")
	language = models.CharField(max_length=50, default="plaintext")
	code = models.TextField(blank=True, default="")

	issue = models.TextField(blank=True, default="")
	concept_gap = models.TextField(blank=True, default="")
	suggestion = models.TextField(blank=True, default="")
	concept_tag = models.CharField(max_length=80, blank=True, default="")

	confidence_score = models.IntegerField(default=0)
	confidence_label = models.CharField(max_length=40, blank=True, default="")
	loop_count = models.IntegerField(default=0)
	conditional_count = models.IntegerField(default=0)
	function_like_count = models.IntegerField(default=0)
	max_tree_depth = models.IntegerField(default=0)
	max_loop_nesting = models.IntegerField(default=0)

	fix_now = models.TextField(blank=True, default="")
	learn_now = models.TextField(blank=True, default="")
	practice_now = models.TextField(blank=True, default="")


class LearnerAction(models.Model):
	created_at = models.DateTimeField(auto_now_add=True)
	snapshot = models.ForeignKey(
		DiagnosticSnapshot,
		related_name="actions",
		on_delete=models.CASCADE,
		null=True,
		blank=True,
	)
	action_type = models.CharField(max_length=80)
	filename = models.CharField(max_length=255, blank=True, default="")
	language = models.CharField(max_length=50, blank=True, default="")
	metadata = models.JSONField(default=dict, blank=True)
