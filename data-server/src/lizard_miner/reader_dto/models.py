"""Lizard CSV row DTO.

Implements §4 of communication/B1_lizard/index_step_general.md.

Lizard emits one row per function with this 11-column header:
  NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class LizardRowDTO(BaseModel):
    nloc: int
    ccn: int
    token: int
    param: int
    length: int
    location: str
    file: str
    function: str
    long_name: str
    start: int
    end: int

    @property
    def class_name(self) -> Optional[str]:
        if "::" in self.function:
            return self.function.split("::", 1)[0]
        return None
