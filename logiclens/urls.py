from django.urls import path
from . import views

urlpatterns = [
    path("", views.editor_page, name="editor_page"),
    path("analysis/", views.analysis_page, name="analysis_page"),
    path("api/analyze/", views.analyze_api_dummy, name="analyze_api_dummy"),
    path("api/run-code/", views.run_code_local, name="run_code_local"),
    path("api/format-code/", views.format_code_local, name="format_code_local"),
    path("api/ast-tree/", views.ast_tree_local, name="ast_tree_local"),
    path("api/progress/", views.progress_timeline_local, name="progress_timeline_local"),
    path("api/action/", views.learner_action_local, name="learner_action_local"),
    path("api/ai-diagnose/", views.ai_diagnose_local, name="ai_diagnose_local"),
    path("api/formatter-status/", views.formatter_status_local, name="formatter_status_local"),
]
