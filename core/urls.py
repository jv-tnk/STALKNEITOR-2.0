from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='index'), # Redireciona o index para o dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
    path('aluno/<str:username>/', views.perfil_aluno, name='perfil_aluno'),
    path('skill-tree/<str:username>/', views.skill_tree, name='skill_tree'),
    path('add-student/', views.add_student, name='add_student'),
    path('submissao/<int:submission_id>/solution/', views.solution_modal, name='solution_modal'),
    path('ranking/', views.ranking, name='ranking'),
    path('ranking/list/', views.ranking_list, name='ranking_list'),
    path('contests/', views.contests_overview, name='contests_overview'),
    path('contests/<str:platform>/<str:contest_id>/', views.contest_detail, name='contest_detail'),
]
