from django.db import models


class UserProfile(models.Model):
	session_key = models.CharField(max_length=255, unique=True)
	display_name = models.CharField(max_length=100, default="Learner")
	credits = models.IntegerField(default=50)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return f"{self.display_name} ({self.session_key[:8]}...)"


class KnowledgeEntry(models.Model):
	PROFICIENCY_CHOICES = [
		("beginner", "Beginner"),
		("developing", "Developing"),
		("strong", "Strong"),
	]

	user = models.ForeignKey(UserProfile, related_name="knowledge", on_delete=models.CASCADE)
	concept_tag = models.CharField(max_length=120)
	proficiency_level = models.CharField(max_length=20, choices=PROFICIENCY_CHOICES, default="beginner")
	practice_count = models.IntegerField(default=0)
	first_seen = models.DateTimeField(auto_now_add=True)
	last_practiced = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("user", "concept_tag")

	def __str__(self):
		return f"{self.concept_tag} ({self.proficiency_level})"


class CreditTransaction(models.Model):
	user = models.ForeignKey(UserProfile, related_name="transactions", on_delete=models.CASCADE)
	amount = models.IntegerField()
	reason = models.CharField(max_length=200)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return f"{self.amount:+d} — {self.reason}"


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


class Note(models.Model):
	user = models.ForeignKey(UserProfile, related_name="notes", on_delete=models.CASCADE)
	snapshot = models.ForeignKey(
		DiagnosticSnapshot,
		related_name="notes",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
	)
	title = models.CharField(max_length=200, default="Untitled Note")
	content = models.TextField(blank=True, default="")
	tags = models.CharField(max_length=300, blank=True, default="")
	filename = models.CharField(max_length=255, blank=True, default="")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-updated_at"]

	def __str__(self):
		return f"{self.title} ({self.user.display_name})"
