from src.enrichment.tagger.anomaly_cohesion import CohesionAnomalyTagger
from src.enrichment.tagger.anomaly_knowledge import KnowledgeAnomalyTagger
from src.enrichment.tagger.anomaly_structuring import StructuringAnomalyTagger
from src.enrichment.tagger.anomaly_testing import TestingAnomalyTagger
from src.enrichment.tagger.author_classifiers import AuthorClassifiersTagger
from src.enrichment.tagger.base import Tagger, TaggingContext, compose_tags
from src.enrichment.tagger.commit_classifiers import CommitClassifiersTagger
from src.enrichment.tagger.file_classifiers import FileClassifiersTagger
from src.enrichment.tagger.issue_pr_classifiers import (
    IssueClassifiersTagger,
    PullRequestClassifiersTagger,
)

__all__ = [
    "Tagger",
    "TaggingContext",
    "compose_tags",
    "CommitClassifiersTagger",
    "FileClassifiersTagger",
    "AuthorClassifiersTagger",
    "IssueClassifiersTagger",
    "PullRequestClassifiersTagger",
    "KnowledgeAnomalyTagger",
    "CohesionAnomalyTagger",
    "StructuringAnomalyTagger",
    "TestingAnomalyTagger",
]
