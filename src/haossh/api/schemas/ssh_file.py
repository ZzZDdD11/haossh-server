"""SSH 文件操作相关 DTO。"""

from pydantic import BaseModel, Field


class FileEntryOut(BaseModel):
    name: str
    path: str
    type: str  # "file" | "directory" | "link"
    size: int
    permissions: str
    modified_at: float | None = Field(default=None, alias="modifiedAt")


class CreateFileRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    path: str
    content: str = ""


class SaveContentRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    path: str
    content: str


class CreateDirectoryRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    path: str


class RenameRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    old_path: str = Field(..., alias="oldPath")
    new_path: str = Field(..., alias="newPath")


class DeleteRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    path: str


class ReadContentRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    path: str


class ReadChunkRequest(BaseModel):
    connection_id: str = Field(..., alias="connectionId")
    path: str
    offset: int
    size: int
