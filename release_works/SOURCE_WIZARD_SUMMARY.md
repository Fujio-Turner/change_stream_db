# Data Source Wizard - Implementation Summary

## Overview
Created a new "Data Source" wizard that follows the same Cloud Storage wizard pattern. Users can now configure and manage replication sources from multiple systems.

## Features

### 1. **Source Systems Supported**
- **Couchbase Sync Gateway** - Replicate from Sync Gateway
- **Couchbase App Service** - Replicate from App Service instances  
- **Couchbase Edge Server** - Replicate from Edge devices
- **CouchDB** - Replicate from Apache CouchDB servers

### 2. **Wizard Workflow**
1. **Landing Page**: Added "Data Source" wizard card with 📡 icon
2. **System Selection**: User picks which source system to configure
3. **Configuration Form**: Dynamic form based on selected system
4. **Summary & Save**: Review and save configuration

### 3. **Saved Sources Management**
- **View Saved Sources**: Table showing all configured sources with system type and save date
- **Add New**: Quick button to add another source
- **Delete**: Remove individual sources
- **Clear All**: Delete all sources at once (with confirmation)
- **Refresh**: Reload sources list

### 4. **Document Storage Structure**
Sources are stored as documents with pattern:
```json
{
  "type": "source",
  "system": "couchbase_sync_gateway",
  "config": {
    "src_name": "my_source",
    "src_gateway_url": "http://localhost:4984",
    "src_database": "my_database",
    ...
  },
  "_meta": {
    "saved_at": "2026-04-19T10:30:00.000+00:00"
  }
}
```

## Implementation Details

### Frontend (web/templates/wizard.html)
- **UI Components**:
  - `sourceWizard` div with selection/config/summary views
  - System selection with icons and descriptions
  - Dynamic form generation per system type
  - Saved sources table with CRUD actions

- **JavaScript Functions**:
  - `startSourceWizard()` - Initialize wizard
  - `srcReset()` - Clear state
  - `srcLoadExistingSources()` - Fetch and display saved sources
  - `srcSelectSystem()` - Handle system selection
  - `srcNextStep()` / `srcPrevStep()` - Navigation
  - `srcRenderConfigForm()` - Build dynamic form
  - `srcShowSummary()` - Display review before save
  - `saveSourceConfig()` - POST to API
  - `srcDeleteSource()` / `srcClearAllSources()` - Delete operations

- **System Definitions** (SRC_SYSTEMS array):
  Each system has config fields with type, placeholder, required flag

### Backend API (web/server.py)
Routes added:
- `GET /api/source/list` - List all saved sources
- `POST /api/source/save` - Save/update a source
- `POST /api/source/delete` - Delete specific source
- `POST /api/source/clear` - Delete all sources

**Handlers**:
- Validate request bodies
- Support both CBLStore (Couchbase) and local file storage
- Local storage uses `ROOT/"sources"/*.json` files
- Return appropriate json_response/error_response

### Database (cbl_store.py)
New CBLStore methods:
- `load_sources()` - Query all type="source" documents
- `save_source(name, doc)` - Create/update source doc
- `delete_source(name)` - Remove specific source
- `clear_all_sources()` - Remove all sources

All methods:
- Use proper logging with `log_event()`
- Include timing metrics
- Handle exceptions gracefully

## Configuration Fields by System

### Couchbase Sync Gateway
- Source Name* (required)
- Sync Gateway URL* (required)
- Database Name* (required)
- Username (optional)
- Password (optional)

### Couchbase App Service
- Source Name* (required)
- App Service Endpoint* (required)
- Bucket Name* (required)
- Scope* (required)
- Collection* (required)
- API Key* (required)

### Couchbase Edge Server
- Source Name* (required)
- Edge Server URL* (required)
- Database Name* (required)
- Replication Filter (optional)

### CouchDB
- Source Name* (required)
- CouchDB URL* (required)
- Database Name* (required)
- Username (optional)
- Password (optional)

## Files Modified
1. `web/templates/wizard.html` - Added Source Wizard UI and JS
2. `web/server.py` - Added 4 API endpoints and handlers
3. `cbl_store.py` - Added 4 source management methods

## Integration Points
- Works with existing Cloud Storage wizard pattern
- Uses same CBL/local storage abstraction
- Integrates with Schema Mapping wizard (will reference selected source)
- Logs events through existing pipeline logging system

## Next Steps
The Source Wizard can now be extended to:
1. Connect selected source to Schema Mapping wizard
2. Add source connectivity testing
3. Implement actual replication logic
4. Add filtering/transform rules per source
