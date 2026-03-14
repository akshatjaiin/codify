from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard_page, name="dashboard_page"),
    path("editor/", views.editor_page, name="editor_page"),
    path("progress/", views.progress_page, name="progress_page"),
    path("knowledge/", views.knowledge_page, name="knowledge_page"),
    path("api/run-code/", views.run_code_local, name="run_code_local"),
    path("api/format-code/", views.format_code_local, name="format_code_local"),
    path("api/ast-tree/", views.ast_tree_local, name="ast_tree_local"),
    path("api/progress/", views.progress_timeline_local, name="progress_timeline_local"),
    path("api/action/", views.learner_action_local, name="learner_action_local"),
    path("api/ai-diagnose/", views.ai_diagnose_local, name="ai_diagnose_local"),
    path("api/formatter-status/", views.formatter_status_local, name="formatter_status_local"),
    path("api/dashboard-stats/", views.dashboard_stats_api, name="dashboard_stats_api"),
    path("api/knowledge/", views.knowledge_api, name="knowledge_api"),
    path("api/knowledge-graph/", views.knowledge_graph_api, name="knowledge_graph_api"),
    path("api/credits/balance/", views.credits_balance_api, name="credits_balance_api"),
    path("api/youtube-recommend/", views.youtube_recommend_api, name="youtube_recommend_api"),
    path("api/video-watched/", views.video_watched_api, name="video_watched_api"),
    path("api/ast-flowchart/", views.ast_flowchart_api, name="ast_flowchart_api"),
    path("api/youtube-search/", views.youtube_search_api, name="youtube_search_api"),
    path("api/notes/", views.notes_api, name="notes_api"),
    path("api/rubber-duck/", views.rubber_duck_api, name="rubber_duck_api"),
    path("api/live-morph/", views.live_morph_api, name="live_morph_api"),
    path("api/memory-context/", views.memory_context_api, name="memory_context_api"),
]
