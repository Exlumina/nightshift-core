"""Resolve Task-board date-only deadlines without inventing a user-specified hour."""
from datetime import date,datetime,time
from zoneinfo import ZoneInfo

def normalize_due(issue,timezone='UTC'):
 result=dict(issue)
 if result.get('due_at') is not None:return result
 value=result.get('dueDate')
 if not value:return result
 if not isinstance(value,str):raise ValueError('Task-board dueDate must be ISO date/time')
 if len(value)==10:
  day=date.fromisoformat(value)
  result['due_at']=datetime.combine(day,time(23,59,59),ZoneInfo(timezone)).timestamp()
  result['deadline_resolution']='Task-board date-only due date, resolved to local end of day in '+timezone
 else:
  parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
  if parsed.tzinfo is None:raise ValueError('timestamp deadline requires an explicit timezone')
  result['due_at']=parsed.timestamp();result['deadline_resolution']='explicit timestamp'
 return result
