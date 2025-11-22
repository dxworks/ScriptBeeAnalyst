from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime

class IssueStatusCategory(BaseModel):
    key: str = Field(..., description="The key of the status category")
    name: str = Field(..., description="The display name of the status category")

class IssueStatus(BaseModel):
    id: str = Field(..., description="The ID of the issue status")
    name: str = Field(..., description="The name of the issue status")
    statusCategory: IssueStatusCategory = Field(..., description="Category info for the status")

class IssueType(BaseModel):
    id: str = Field(..., description="The ID of the issue type")
    name: str = Field(..., description="The name of the issue type")
    description: str = Field(..., description="Description of the issue type")
    isSubTask: bool = Field(..., description="Whether this type is a sub-task")

class ChangeItem(BaseModel):
    field: str
    from_: Optional[str] = Field(None, alias="from")
    fromString: Optional[str] = None
    to: Optional[str] = None
    toString: Optional[str] = None

class Change(BaseModel):
    changedFields: List[str] = Field(..., description="The fields changed")
    created:datetime = Field(..., description="The date the issue was created")
    id:int = Field(..., description="The ID of the change")
    items:List[ChangeItem] = Field(..., description="The items changed")
    userId:str = Field(..., description="The user id of the change")

class Comment(BaseModel):
    body: str = Field(..., description="The comment body")
    created: datetime = Field(..., description="The date the comment was created")
    updateUserId: str = Field(... , alias="updateUserId")
    updated:datetime = Field(..., description="The date the comment was updated")
    userId:str = Field(..., description="The user id of the comment")

class Issue(BaseModel): # check issues with this class on a certain json file use /scripts/JSON_structure_extractor.py
    # COMMON fields
    changes: List[Change] = []
    comments: List[Comment] = []
    created: datetime = Field(..., description="The date the issue was created")
    customFields: Dict[str, object] = {} # usually empty TODO: find an example
    description: Optional[str] = Field()
    id: int = Field()
    key: str = Field()
    priority: str = Field()
    reporterId: str = Field()
    self_: str = Field(..., alias="self")
    status: IssueStatus = Field()
    subTasks: List[str] = []
    summary: str = Field()
    timeEstimate: Optional[int] = Field(None)
    timeSpent: Optional[int] = Field(None)
    issueType: str = Field(..., alias="type") # TODO: issue type e string cu numele issue type-ului
    typeId: int = Field()
    updated: datetime = Field()

    # OPTIONAL fields
    assigneeId: Optional[str] = Field(None)
    creatorId : Optional[str] = Field(None)
    parent: Optional[str] = Field(None) # key of other issue

    # taken out of C# extractor for ScriptBee
    # TODO vezi care i faza cu astea
    # creator: Optional[Dict] = None
    # assignee: Optional[Dict] = None
    # reporter: Optional[Dict] = None
    # parentId: Optional[str] = None
    # components: List[Dict] = []

class User(BaseModel):
    avatarUrl: str = Field(..., alias="avatarUrl")
    key: str = Field(..., description="The key of the user")
    name:str = Field(..., description="The name of the user")
    self_:str = Field(..., alias="self")

class JsonFileFormatJira(BaseModel):
    issueStatuses: List[IssueStatus] = []
    issueTypes: List[IssueType] = []
    issues: List[Issue] = []
    users: List[User] = []

Issue.model_rebuild()