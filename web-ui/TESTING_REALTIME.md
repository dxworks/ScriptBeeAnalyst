# Testing Realtime Status Updates & Data Server Integration

## Overview

This document describes how to test the Supabase Realtime status updates and data-server integration implemented in the Angular frontend.

## Prerequisites

1. **Supabase** running on `http://localhost:8000`
2. **Data Server** running on `http://localhost:8001`
3. **Web UI** running on `http://localhost:4200`
4. User account created and logged in

## What Was Implemented

### 1. Data Server Service (`core/services/data-server.service.ts`)
- HTTP client for data-server API
- JWT authentication using Supabase session token
- Endpoints:
  - `buildProject(projectId)` - Build project graph
  - `unloadProject(projectId)` - Unload from memory
  - `getHealth()` - Health check

### 2. Realtime Subscription (`core/services/project.service.ts`)
- Subscribes to `projects` table changes filtered by user_id
- Listens for INSERT, UPDATE, DELETE events
- Automatically updates local state when changes occur
- Subscription lifecycle:
  - Created on component init
  - Cleaned up on component destroy

### 3. Project Detail Component Updates
- Calls data-server build endpoint when "Process Data" is clicked
- Shows loading spinner during processing
- Disables button during processing
- Receives status updates via realtime (no polling!)
- Shows toast notifications on status changes:
  - Success when status changes to 'ready'
  - Error when status changes to 'error'
  - Info for other transitions

## Test Scenarios

### Scenario 1: Build Project Graph (Happy Path)

**Steps:**
1. Navigate to `/projects` and select a project with status 'draft'
2. Go to the "Files" section
3. Ensure at least one file is uploaded (git.iglog, github.json, or jira.json)
4. Click "Process Data" button

**Expected Behavior:**
1. Button shows spinner and "Processing..." text
2. Button becomes disabled
3. Project status badge updates to "Processing" (yellow)
4. Status card in description section shows "Building Graph" with spinner
5. When data-server completes:
   - Status automatically updates to "Ready" (green) via realtime
   - Success toast appears: "Project graph built successfully!"
   - Button disappears (only shown for 'draft' status)

**What to Observe:**
- No page refresh required
- Status updates happen automatically
- Console logs show realtime events:
  ```
  Realtime subscription status: SUBSCRIBED
  Project updated: {id: "...", status: "processing", ...}
  Project updated: {id: "...", status: "ready", ...}
  ```

### Scenario 2: Build Project Graph (Error)

**Steps:**
1. Stop the data-server (simulate backend failure)
2. Navigate to project with status 'draft'
3. Go to "Files" section
4. Click "Process Data"

**Expected Behavior:**
1. Button shows spinner briefly
2. Error toast appears with connection error message
3. Status updates to "error" (red)
4. Status card shows "Error" with retry button

**Alternative:** If data-server is running but returns an error:
- Same behavior but error message from data-server

### Scenario 3: Realtime Updates from External Source

**Purpose:** Test that realtime subscription works for updates from other sources (e.g., data-server updating status)

**Steps:**
1. Open project detail page
2. In Supabase Studio (http://localhost:8000), go to Table Editor
3. Manually update the project's status field:
   - Change from 'draft' to 'processing'
   - Then from 'processing' to 'ready'

**Expected Behavior:**
1. UI updates immediately without refresh
2. Status badge changes color
3. Status card in description section updates
4. Toast notifications appear for status transitions

### Scenario 4: Multiple Tabs/Windows

**Steps:**
1. Open project detail page in two browser tabs
2. In Tab 1, click "Process Data"
3. Observe Tab 2

**Expected Behavior:**
- Both tabs receive realtime updates
- Both show status change simultaneously
- Both show toast notifications

### Scenario 5: Navigation During Processing

**Steps:**
1. Click "Process Data" to start building
2. Immediately navigate to another project or section
3. Wait a few seconds
4. Return to the original project

**Expected Behavior:**
- Subscription is cleaned up when component is destroyed
- New subscription is created when returning
- Status reflects current state from database

### Scenario 6: Retry After Error

**Steps:**
1. Project in 'error' status
2. Navigate to "Chat" section
3. Click "Retry" button

**Expected Behavior:**
- Calls same build endpoint as "Process Data"
- Status updates to 'processing', then 'ready' or 'error'
- Toast notifications appear

## Debugging

### Enable Realtime Logs

Open browser console and look for:
```
Realtime subscription status: SUBSCRIBED
Project updated: {id: "...", status: "ready", ...}
```

### Check Network Requests

In browser DevTools Network tab:
1. **Supabase WebSocket** - Should show "ws://localhost:8000/realtime/v1/websocket"
2. **Data Server API** - Look for POST to `http://localhost:8001/projects/{id}/build`

### Common Issues

**Issue:** Realtime not working
- Check Supabase is running with realtime enabled
- Verify user is authenticated (session exists)
- Check console for subscription errors

**Issue:** Data server connection fails
- Verify data-server is running on port 8001
- Check CORS configuration allows localhost:4200
- Verify JWT token is being sent in Authorization header

**Issue:** Status stuck on 'processing'
- Check data-server logs for errors
- Verify data-server has access to Supabase (for file download)
- Manually update status in database to 'error' or 'ready'

**Issue:** Toast notifications not appearing
- Check ToastContainerComponent is in app.component.html
- Verify effect in component is tracking status changes
- Check console for errors

## Architecture Flow

```
User clicks "Process Data"
    ↓
ProjectDetailComponent.onProcessData()
    ↓
ProjectService.updateProjectStatus('processing')
    ↓
Supabase DB: UPDATE projects SET status='processing'
    ↓
Realtime broadcast to all subscribers
    ↓
ProjectService receives UPDATE event
    ↓
Updates projectsSignal (local state)
    ↓
Angular change detection triggers
    ↓
UI updates automatically (badge, card, etc.)
    ↓
DataServerService.buildProject()
    ↓
POST to data-server /projects/{id}/build
    ↓
Data-server processes and updates DB status
    ↓
[Realtime loop repeats for status='ready'/'error']
```

## Manual Testing Checklist

- [ ] Build project from draft → processing → ready
- [ ] Build project from draft → processing → error (stop data-server)
- [ ] Retry processing from error status
- [ ] Observe realtime updates in multiple tabs
- [ ] Navigate away during processing and return
- [ ] Check console logs for subscription lifecycle
- [ ] Verify toast notifications appear for status changes
- [ ] Test with data-server unavailable
- [ ] Test with missing files (should show error)
- [ ] Verify button is disabled during processing

## Next Steps

After realtime integration:
1. Implement auto-resume when opening Chat on 'idle' project
2. Add unload functionality (call dataServerService.unloadProject)
3. Add visual indicator for projects loaded in memory
4. Implement periodic health check to show loaded projects
5. Handle session expiration (realtime subscription will fail, need to re-auth)

## Files Modified

```
web-ui/src/app/
├── app.config.ts                              # Added provideHttpClient()
├── core/services/
│   ├── data-server.service.ts                 # NEW - HTTP client for data-server
│   └── project.service.ts                     # MODIFIED - Realtime subscription
├── pages/project-detail/
│   ├── project-detail.component.ts            # MODIFIED - Integration & lifecycle
│   └── project-detail.component.html          # MODIFIED - Loading states
└── environments/
    └── environment.ts                         # MODIFIED - Added dataServerUrl
```
