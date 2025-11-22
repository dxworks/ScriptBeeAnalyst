from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class CommitNodeDTO:
    """
    DTO care reprezintă un nod (commit) într-un arbore de commit-uri.
    Echivalent al clasei Kotlin:
        class CommitNodeDTO(val id: String,
                            val parents: List<CommitNodeDTO>) {
            val children: MutableList<CommitNodeDTO> = ArrayList()
            fun addChild(commitNodeDTO: CommitNodeDTO) {
                children.add(commitNodeDTO)
            }
        }
    """
    id: str
    parents: List["CommitNodeDTO"]
    # children sunt inițial goale și se umplu prin add_child
    children: List["CommitNodeDTO"] = field(default_factory=list)

    def add_child(self, commit_node: "CommitNodeDTO") -> None:
        """
        Adaugă un copil la lista de children.
        Observație: comportamentul e identic cu metoda Kotlin originală --
        nu adaugă automat acest nod la parents-ul copilului.
        """
        self.children.append(commit_node)

    def __repr__(self) -> str:
        return f"CommitNodeDTO(id={self.id!r}, parents_count={len(self.parents)}, children_count={len(self.children)})"
