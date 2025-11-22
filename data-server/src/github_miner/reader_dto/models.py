from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class Language(BaseModel):
    name: str = Field(...)

class UserGithub(BaseModel):
    # COMMON
    avatarUrl: str = Field(...)
    url: str = Field(...)

    #OPTIONAL  (repo owner doesn't have login)
    login: Optional[str] = Field(None)
    name: Optional[str]  = Field(None) # neds to be optional to read fom  PullRequest assignees

class RepositoryInfo(BaseModel):
    createdAt: datetime = Field()
    description:str = Field()
    fullPath:str = Field()
    id:str = Field()
    languages:List[Language] = Field()
    name:str = Field()
    owner:UserGithub = Field()
    updatedAt:datetime = Field()

class Branch(BaseModel):
    commitUrl: str = Field(...)
    name: str = Field(...)
    sha: str = Field(...)

class Comment(BaseModel):
    author: Optional[UserGithub] = Field(None)
    body: str = Field()
    createdAt: datetime = Field()
    updatedAt: datetime = Field()
    url: str = Field()

class CommitGitHubMiner(BaseModel):
    author: Optional[UserGithub] = Field(None)
    changedFiles: int = Field()
    date: datetime = Field()
    message: str = Field()
    sha: str = Field()
    url: str = Field()

class Label(BaseModel):
    description: Optional[str] = Field(None)
    name: str = Field()

class RequestedReviewer(BaseModel):
    requestedReviewer : UserGithub = Field()

class Review(BaseModel):
    body :str = Field(...)
    comments : List[Comment] = Field(...)
    submittedAt:Any = Field(...)
    state: str = Field(...)
    user:Optional[UserGithub] = Field(None)

class PullRequest(BaseModel):
    # COMMON fields
    assignees: List[UserGithub] = []
    base: Branch = Field()
    body: str = Field()
    changedFiles: int = Field()
    closedAt: Optional[datetime] = Field(None)
    comments: List[Comment] = Field()
    commits: List[CommitGitHubMiner] = Field()
    createdAt: datetime = Field()
    labels: List[Label] = Field()
    mergedAt: Optional[datetime] = Field()
    number: int = Field()

    reviewRequests: List[RequestedReviewer] = Field()
    reviews: List[Review] = Field()
    state: str = Field()
    title: str = Field()
    updatedAt: Optional[datetime] = Field()

    # OPTIONAL fields (alphabetical)
    createdBy: Optional[UserGithub] = Field(None)
    head: Optional[Branch] = Field(None)
    mergedBy: Optional[UserGithub] = Field(None)



class JsonFileFormatGithub(BaseModel):
    repositoryInfo:RepositoryInfo
    issues:Any #TODO: in zeppelin was empty
    pullRequests:List[PullRequest]