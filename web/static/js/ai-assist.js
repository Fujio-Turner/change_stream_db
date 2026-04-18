/**
 * ai-assist.js — Shared AI-assist logic for schema.html and wizard.html.
 * Consolidates duplicated instructions, field analysis, context building,
 * and UI helpers into a single module loaded via <script>.
 */

/* ───────────────────────────────────────────────────────────────────────
   1.  AI_INSTRUCTIONS — the full instruction text sent to the LLM
   ─────────────────────────────────────────────────────────────────────── */

var AI_INSTRUCTIONS = [
  'You are generating a Changes Worker schema mapping JSON.',
  'Return ONLY valid JSON — no markdown, no code fences, no commentary outside the JSON.',
  '',
  '## STRICT OUTPUT RULES (violations will break the import parser)',
  '',
  'RULE 1: ALL JSONPath expressions MUST start with "$." — never strip the dollar sign or dot.',
  '  CORRECT: "$._id", "$.status", "$.items", "$.address.city"',
  '  WRONG:   "_id", "status", "items", "address.city"',
  '',
  'RULE 2: Transform functions MUST repeat the source path as the first argument inside parentheses.',
  '  CORRECT: "to_date($.order_date)"   — path is INSIDE the function call',
  '  WRONG:   "to_date()"               — parser will fail without the path',
  '  WRONG:   "to_date(order_date)"     — missing "$." prefix',
  '',
  'RULE 3: Child table "primary_key" — if the target_tables definition specifies a primary_key value (e.g. "id"), use that value exactly. This is typically a database-generated surrogate key (auto-increment) and does NOT need to appear in the "columns" mapping. If target_tables has no primary_key or it is empty, use "" (empty string).',
  '',
  'RULE 4: Child tables MUST include ALL of: "parent", "foreign_key", "source_array", "replace_strategy".',
  '',
  'RULE 5: Every table MUST include ALL of: "name", "primary_key", "columns", "on_delete".',
  '',
  'RULE 6: Include an "_explanation" field (array of strings) explaining your reasoning for each mapping decision.',
  '',
  'RULE 7: Only map fields that exist in source_fields or target_tables. Do not invent fields.',
  '',
  '## How mappings work',
  '',
  'A mapping tells the Changes Worker how to transform a Couchbase/Sync Gateway document into SQL rows or a JSON object.',
  '',
  '### Tables mode (output_mode = "tables")',
  '',
  'The top-level object has:',
  '  - "source": { "match": { "field": "<doc_field>", "value": "<value>" } } — only documents where this field equals this value are processed.',
  '  - "tables": [ ... ] — an ordered array of table definitions.',
  '',
  'Each table object has these EXACT fields (use these field names, not alternatives):',
  '  - "name" (string, REQUIRED): The SQL table name.',
  '  - "primary_key" (string, REQUIRED): The column NAME that serves as the primary key. For parent tables, this column MUST also appear in "columns" with a JSONPath (e.g. "doc_id" mapped to "$._id"). For child tables, use the value from target_tables if one is defined (e.g. "id" for a DB auto-increment column — this does NOT need a columns entry), otherwise use "" (empty string).',
  '  - "columns" (object, REQUIRED): Maps SQL column names to JSONPath expressions or transform objects.',
  '  - "on_delete" (string): What to do when the source doc is deleted. Usually "delete".',
  '  - "parent" (string): For child tables ONLY — the name of the parent table this child belongs to.',
  '  - "foreign_key" (object): For child tables ONLY — { "column": "<child_fk_col>", "references": "<parent_pk_col>" }.',
  '  - "source_array" (string): For child tables ONLY — JSONPath to the array in the parent doc that produces child rows (e.g. "$.items").',
  '  - "replace_strategy" (string): For child tables — usually "delete_insert" (delete old child rows then insert new ones on update).',
  '',
  '### Column value formats',
  '',
  'Each column value can be:',
  '  - A simple JSONPath string: "$.field_name" — extracts the value directly.',
  '  - A transform object: { "path": "$.field_name", "transform": "function_name($.field_name)" } — extracts then transforms.',
  '',
  '### Transform syntax (IMPORTANT — include the path in the function call)',
  '',
  'Transforms MUST include the source JSONPath as the first argument inside the function parentheses.',
  'The engine strips the path argument internally, but it MUST be present in the mapping JSON.',
  '',
  'CORRECT examples:',
  '  { "path": "$.order_date", "transform": "to_date($.order_date)" }',
  '  { "path": "$.price", "transform": "to_float($.price)" }',
  '  { "path": "$.name", "transform": "uppercase($.name)" }',
  '  { "path": "$.name", "transform": "trim($.name)" }',
  '  { "path": "$.total", "transform": "to_decimal($.total,2)" }',
  '  { "path": "$.status", "transform": "coalesce($.status,unknown)" }',
  '  { "path": "$.tags", "transform": "json_stringify($.tags)" }',
  '',
  'split() with array index — extract part of a compound key:',
  '  Given _id = "invoice::12345":',
  '  { "path": "$._id", "transform": "split($._id,\"::\")[0]" }  → "invoice"  (the type prefix)',
  '  { "path": "$._id", "transform": "split($._id,\"::\"")[1]" }  → "12345"    (the actual ID)',
  '  This is common when _id encodes the document type as a prefix (e.g. "order::99", "user::abc").',
  '',
  'WRONG examples (do NOT do this):',
  '  { "path": "$.order_date", "transform": "to_date()" }        ← MISSING the path argument',
  '  { "path": "$.price", "transform": "to_float()" }            ← MISSING the path argument',
  '  { "path": "$._id", "transform": "split(,\"::\")[0]" }       ← MISSING the path argument',
  '',
  'Chaining example:',
  '  { "path": "$.name", "transform": "trim($.name).lowercase()" }',
  '',
  '### JSONPath rules',
  '',
  '  - Parent table columns use paths relative to the root document: "$._id", "$.status", "$.nested.field".',
  '  - Child table columns use paths relative to each array element: "$.product_id", "$.qty" (NOT "$.items[].product_id").',
  '  - The exception: a child column referencing a PARENT field (like a foreign key) still uses the root path: "$._id".',
  '',
  '### Primary Key & Foreign Key linkage (IMPORTANT)',
  '',
  'The "primary_key" field is a COLUMN NAME, not a value. That column MUST also exist inside "columns" with a JSONPath.',
  'The chain works like this:',
  '',
  '  Parent table:',
  '    "primary_key": "doc_id"          ← this is a column name',
  '    "columns": { "doc_id": "$._id" } ← the column MUST appear here with a source path',
  '    Result: the primary key value comes from $._id in the source document.',
  '',
  '  Child table:',
  '    "primary_key": "id"                        ← use value from target_tables if defined (DB auto-increment), otherwise ""',
  '    "foreign_key": { "column": "order_doc_id", "references": "doc_id" }',
  '    "columns": { "order_doc_id": "$._id" }     ← FK column MUST also appear in columns',
  '    Note: "id" does NOT appear in "columns" because it is generated by the database, not mapped from JSON.',
  '    The FK column "order_doc_id" uses the SAME source path "$._id" as the parent\'s PK column.',
  '    "references": "doc_id" points to the parent column NAME (not the path).',
  '',
  '  Summary: parent.primary_key → parent.columns[pk_name] → $.source_path',
  '           child.foreign_key.column → child.columns[fk_name] → SAME $.source_path',
  '           child.foreign_key.references → parent.primary_key (by column name)',
  '',
  '### Parent / Child table relationships',
  '',
  '  - The FIRST table is typically the parent (no "parent", "foreign_key", or "source_array" fields).',
  '  - Child tables MUST include "parent", "foreign_key", "source_array", and "replace_strategy".',
  '  - Example: If the parent table "orders" has primary_key "doc_id" mapped to "$._id", and the child "order_items" iterates over "$.items",',
  '    then order_items needs: "parent": "orders", "foreign_key": { "column": "order_doc_id", "references": "doc_id" }, "source_array": "$.items".',
  '',
  '### Nested objects',
  '',
  'When the source document contains a nested object like "address": { "city": "Austin", "state": "TX", "postalCodes": ["78764","78745"] }:',
  '  - Access individual nested fields with dot notation: "$.address.city", "$.address.state".',
  '  - To store the entire object as a JSON string in a single column, use: { "path": "$.address", "transform": "json_stringify($.address)" }.',
  '  - To access an array inside a nested object for a child table, use: "source_array": "$.address.postalCodes".',
  '',
  '### Simple (primitive) arrays',
  '',
  'When the source document contains an array of simple values like "sports": ["water", "land"] or "tags": ["vip", "active"]:',
  '',
  'Option A — Store as a JSON string in a single column (simplest):',
  '  "sports_json": { "path": "$.sports", "transform": "json_stringify($.sports)" }',
  '',
  'Option B — Create a child table with one row per array element:',
  '  {',
  '    "name": "user_sports",',
  '    "primary_key": "",',
  '    "parent": "users",',
  '    "foreign_key": { "column": "user_doc_id", "references": "doc_id" },',
  '    "source_array": "$.sports",',
  '    "replace_strategy": "delete_insert",',
  '    "columns": {',
  '      "user_doc_id": "$._id",',
  '      "value": "$"',
  '    },',
  '    "on_delete": "delete"',
  '  }',
  '  Note: Use "$" (bare dollar sign) as the path when the array element IS the value itself (not an object).',
  '',
  'Choose Option A unless the target_tables already defines a separate child table for that array.',
  '',
  '### JSON mode (output_mode = "json")',
  '',
  'Instead of "tables", use "output_format": "json" and "mapping": { "targetKey": "$.source.path", ... }.',
  '',
  '### Transforms',
  '',
  'Use transforms from the available_transforms list. ALWAYS include the source path as the first argument.',
  'Common examples (assuming the column path is $.field_name):',
  '  - uppercase($.field_name), lowercase($.field_name) — string case conversion',
  '  - to_int($.field_name), to_float($.field_name) — type conversion',
  '  - to_date($.field_name), to_iso8601($.field_name) — date parsing',
  '  - trim($.field_name) — remove whitespace',
  '  - coalesce($.field_name,0) — default value if null',
  '  - json_stringify($.field_name) — convert arrays/objects to JSON string',
  '  - split($._id,"::")[0] — extract part of a compound key (e.g. "invoice::12345" → "invoice")',
  'Only use a transform when the data type or format needs conversion. Do not add transforms unnecessarily.',
  '',
  '## How to use this context',
  '',
  'This payload gives you four layers of information to produce an accurate mapping:',
  '',
  '### Layer 1: Document Structure (document_structure)',
  'Pre-analyzed breakdown of the sample document:',
  '  - metadata_fields: system fields like _id and _rev.',
  '  - scalar_fields: simple value fields that map directly to columns.',
  '  - nested_objects: objects that can be flattened with dot notation or stored via json_stringify().',
  '  - arrays_of_objects: arrays whose elements have named fields — strong candidates for child tables.',
  '  - arrays_of_primitives: simple arrays — use json_stringify() or a child table with "$" as the column path.',
  '',
  '### Layer 2: Filter Logic (source_match)',
  'Which documents this mapping applies to. Use this to set the source.match in your output.',
  '',
  '### Layer 3: Target Schema (target_tables + table_relationships)',
  'The intended SQL structure including table names, primary keys, and parent/child relationships.',
  'Map source fields into these tables. If target_tables is empty, infer reasonable tables from the document structure.',
  '',
  'table_relationships spells out the EXACT PK/FK chain:',
  '  - parent_primary_key_column: the column name used as PK in the parent.',
  '  - parent_primary_key_source_path: the JSONPath that provides the PK value (e.g. "$._id").',
  '  - child_foreign_key_column: the column name in the child table that references the parent PK.',
  '  - child_foreign_key_source_path: MUST be the SAME JSONPath as the parent PK (e.g. "$._id").',
  '  - references: the parent column name that the FK points to.',
  'Use these to wire up primary_key, foreign_key, and the matching columns entries.',
  '',
  '### Layer 4: Data Integrity Strategy',
  'For child tables created from arrays, always set:',
  '  - replace_strategy: "delete_insert" — on document update, delete all old child rows and re-insert. Do not attempt partial updates.',
  '  - on_delete: "delete" — when the source document is deleted from the NoSQL bucket, remove all corresponding SQL rows.',
  'These ensure the SQL tables stay in sync with the NoSQL source of truth.',
  '',
  '### Layer 5: Transform Hints (transform_hints)',
  'Auto-detected type mismatches between source JSON types and likely SQL column types.',
  'For example, a date stored as a string should use to_date(). Apply these suggestions where relevant.',
  '',
  '### JSONPath reminder for child tables',
  'Child table columns use paths relative to each array element (e.g. "$.qty", "$.product_id").',
  'EXCEPTION: a child column that references a PARENT field (like a foreign key to doc_id) must use the root path (e.g. "$._id").'
].join('\n');


/* ───────────────────────────────────────────────────────────────────────
   2.  AI_RESPONSE_FORMAT — expected output structure for the LLM
   ─────────────────────────────────────────────────────────────────────── */

var AI_RESPONSE_FORMAT = {
  _note: [
    'Return a JSON object matching EXACTLY this structure.',
    'Use the exact field names shown — do NOT rename fields.',
    'Every JSONPath MUST start with "$." — e.g. "$._id", "$.status", "$.order_date".',
    'Every transform MUST include the path inside the function — e.g. "to_date($.order_date)" NOT "to_date()".',
    'Child tables: use the primary_key from target_tables if defined (e.g. "id" for auto-increment), otherwise "". MUST include parent, foreign_key, source_array, replace_strategy.',
    'Always include the _explanation field as an array of strings.'
  ],
  tables_mode_example: {
    _explanation: [
      'Created parent table "orders" — primary_key is "doc_id" which is a column name, and columns.doc_id maps to "$._id".',
      'Applied to_date($.order_date) transform — note the path $.order_date is INSIDE the function call.',
      'Created child table "order_items" with primary_key "id" (from target_tables — DB auto-increment, not in columns), iterating over $.items array.',
      'Child FK linkage: order_doc_id column maps to "$._id" (same source path as parent PK), and references "doc_id" (parent column name).',
      'Used replace_strategy "delete_insert" and on_delete "delete" for sync integrity.'
    ],
    source: { match: { field: 'type', value: 'order' } },
    tables: [
      {
        name: 'orders',
        primary_key: 'doc_id',
        columns: {
          doc_id: '$._id',
          rev: '$._rev',
          status: '$.status',
          order_date: { path: '$.order_date', transform: 'to_date($.order_date)' },
          total: '$.total'
        },
        on_delete: 'delete'
      },
      {
        name: 'order_items',
        primary_key: 'id',
        parent: 'orders',
        foreign_key: { column: 'order_doc_id', references: 'doc_id' },
        source_array: '$.items',
        replace_strategy: 'delete_insert',
        columns: {
          order_doc_id: '$._id',
          product_id: '$.product_id',
          qty: '$.qty',
          price: '$.price'
        },
        on_delete: 'delete'
      }
    ]
  },
  json_mode_example: {
    source: { match: { field: 'type', value: 'example' } },
    output_format: 'json',
    mapping: {
      targetKey: '$.source.path',
      transformedKey: { path: '$.raw_value', transform: 'to_int($.raw_value)' }
    }
  }
};


/* ───────────────────────────────────────────────────────────────────────
   3.  aiAnalyzeFields — classify source fields & generate transform hints
   ─────────────────────────────────────────────────────────────────────── */

function aiAnalyzeFields(sourceFieldsList) {
  var docStructure = {
    metadata_fields: [],
    scalar_fields: [],
    nested_objects: [],
    arrays_of_objects: [],
    arrays_of_primitives: []
  };

  for (var si = 0; si < sourceFieldsList.length; si++) {
    var sf = sourceFieldsList[si];
    var p = sf.path;
    if (p === '$._id' || p === '$._rev') {
      docStructure.metadata_fields.push(p);
    } else if (sf.type === 'array') {
      var nextF = sourceFieldsList[si + 1];
      if (nextF && nextF.path.indexOf(p + '[]') === 0 && nextF.path.indexOf('.') > p.length + 2) {
        docStructure.arrays_of_objects.push({ path: p, hint: 'Array of objects — 1:N relationship, candidate for child table with foreign key back to parent' });
      } else {
        docStructure.arrays_of_primitives.push({ path: p, hint: 'Array of simple values — use json_stringify() or child table with "$" path' });
      }
    } else if (sf.type === 'object') {
      docStructure.nested_objects.push({ path: p, hint: 'Nested object — use dot notation for sub-fields or json_stringify() for whole object' });
    } else {
      docStructure.scalar_fields.push(p);
    }
  }

  var transformHints = [];
  for (var ti = 0; ti < sourceFieldsList.length; ti++) {
    var tf = sourceFieldsList[ti];
    if (tf.type === 'string' && tf.sample_value) {
      if (/^\d{4}-\d{2}-\d{2}/.test(tf.sample_value)) {
        transformHints.push({ field: tf.path, source_type: 'string (ISO date)', suggested_transform: 'to_date()', reason: 'Value looks like an ISO date string' });
      } else if (/^-?\d+(\.\d+)?$/.test(tf.sample_value)) {
        transformHints.push({ field: tf.path, source_type: 'string (numeric)', suggested_transform: 'to_int() or to_float()', reason: 'Value is a string containing a number' });
      } else if (/^(true|false)$/i.test(tf.sample_value)) {
        transformHints.push({ field: tf.path, source_type: 'string (boolean)', suggested_transform: 'to_bool()', reason: 'Value is a string containing a boolean' });
      }
    }
  }

  return { docStructure: docStructure, transformHints: transformHints };
}


/* ───────────────────────────────────────────────────────────────────────
   4a. aiCategorizeTransforms — bucket raw transform strings by category
   ─────────────────────────────────────────────────────────────────────── */

function aiCategorizeTransforms(transforms) {
  var cats = { 'String': [], 'Numeric': [], 'Date / Time': [], 'Array / Object': [], 'Encoding / Hash': [], 'Conditional': [] };
  var rules = {
    'String': /^(trim|ltrim|rtrim|uppercase|lowercase|camelcase|propercase|concat|replace|replace_regex|strip_chars|pad_left|pad_right|substr|split|join|length|urlencode|urldecode)\(/,
    'Numeric': /^(to_int|to_float|to_decimal|to_string|round|ceil|floor|abs|coalesce)\(/,
    'Date / Time': /^(to_date|to_iso8601|to_epoch|from_epoch|format_date|parse_date|date_add|date_diff|truncate_date|now)\(/,
    'Array / Object': /^(flatten|slice|keys|values)\(/,
    'Encoding / Hash': /^(json_safe|json_parse|json_stringify|base64_encode|base64_decode|md5|sha256|uuid)\(/,
    'Conditional': /^(if)\(/
  };
  for (var i = 0; i < transforms.length; i++) {
    var t = transforms[i];
    if (!t) continue;
    for (var cat in rules) {
      if (rules[cat].test(t)) { cats[cat].push(t); break; }
    }
  }
  return cats;
}


/* ───────────────────────────────────────────────────────────────────────
   4b. aiBuildContext — assemble the full context payload for the LLM
   ─────────────────────────────────────────────────────────────────────── */

function aiBuildContext(opts) {
  var sampleDoc      = opts.sampleDoc      || null;
  var sourceFields   = opts.sourceFields   || [];
  var targetTables   = opts.targetTables   || [];
  var outputMode     = opts.outputMode     || 'tables';
  var sourceMatch    = opts.sourceMatch    || null;
  var jsonMappings   = opts.jsonMappings   || null;
  var transforms     = opts.transforms     || [];

  var analysis = aiAnalyzeFields(sourceFields);

  var transformsByCategory = aiCategorizeTransforms(transforms);

  // Build table_relationships — explicit PK/FK linkage for the AI
  var tableRelationships = [];
  if (targetTables.length) {
    // Index tables by name for lookup
    var tablesByName = {};
    for (var ri = 0; ri < targetTables.length; ri++) {
      tablesByName[targetTables[ri].table_name] = targetTables[ri];
    }
    for (var rj = 0; rj < targetTables.length; rj++) {
      var tbl = targetTables[rj];
      if (tbl.parent_table && tbl.foreign_key && tablesByName[tbl.parent_table]) {
        var parentTbl = tablesByName[tbl.parent_table];
        var parentPkCol = parentTbl.primary_key;
        var parentPkPath = parentPkCol && parentTbl.columns[parentPkCol]
          ? (parentTbl.columns[parentPkCol].current_mapping || parentTbl.columns[parentPkCol])
          : '(unknown — define the PK column in the parent table)';
        tableRelationships.push({
          parent_table: tbl.parent_table,
          parent_primary_key_column: parentPkCol,
          parent_primary_key_source_path: parentPkPath,
          child_table: tbl.table_name,
          child_foreign_key_column: tbl.foreign_key.column,
          child_foreign_key_source_path: parentPkPath + ' (MUST use the same source path as the parent PK)',
          references: tbl.foreign_key.references || parentPkCol,
          source_array: tbl.source_array || '(define the JSONPath to the array that produces child rows)'
        });
      }
    }
  }

  var context = {
    _instructions: AI_INSTRUCTIONS,
    output_mode: outputMode,
    source_match: sourceMatch,
    sample_document: sampleDoc || '(no sample document provided — paste one in the Source JSON panel)',
    source_fields: sourceFields.length ? sourceFields : '(paste a sample JSON to extract fields)',
    document_structure: analysis.docStructure,
    transform_hints: analysis.transformHints.length ? analysis.transformHints : '(no type mismatches detected — transforms may still be needed based on target schema)',
    target_tables: targetTables.length ? targetTables : '(no target tables defined yet — import from DB or add manually)',
    table_relationships: tableRelationships.length ? tableRelationships : '(no parent/child relationships detected — define parent, foreign_key, source_array on child tables)',
    current_json_mappings: jsonMappings,
    available_transforms: transformsByCategory,
    response_format: AI_RESPONSE_FORMAT
  };

  return context;
}


/* ───────────────────────────────────────────────────────────────────────
   5.  aiSwitchTab — generic export / import tab toggler
   ─────────────────────────────────────────────────────────────────────── */

function aiSwitchTab(tabId, exportId, importId, exportPanelId, importPanelId) {
  document.getElementById(exportId).classList.toggle('tab-active', tabId === 'export');
  document.getElementById(importId).classList.toggle('tab-active', tabId === 'import');
  var ep = document.getElementById(exportPanelId);
  var ip = document.getElementById(importPanelId);
  if (tabId === 'export') {
    ep.classList.remove('hidden'); ep.classList.add('flex');
    ip.classList.add('hidden');    ip.classList.remove('flex');
  } else {
    ep.classList.add('hidden');    ep.classList.remove('flex');
    ip.classList.remove('hidden'); ip.classList.add('flex');
  }
}


/* ───────────────────────────────────────────────────────────────────────
   6.  aiCopy — copy textarea contents to clipboard
   ─────────────────────────────────────────────────────────────────────── */

function aiCopy(textareaId, feedbackFn) {
  var el = document.getElementById(textareaId);
  navigator.clipboard.writeText(el.value).then(function() {
    if (feedbackFn) feedbackFn();
  }).catch(function() {
    el.select();
    document.execCommand('copy');
    if (feedbackFn) feedbackFn();
  });
}


/* ───────────────────────────────────────────────────────────────────────
   7.  aiDownload — download textarea contents as a JSON file
   ─────────────────────────────────────────────────────────────────────── */

function aiDownload(textareaId, filename) {
  var content = document.getElementById(textareaId).value;
  var blob = new Blob([content], { type: 'application/json' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename || 'ai_mapping_context.json';
  a.click();
  URL.revokeObjectURL(a.href);
}


/* ───────────────────────────────────────────────────────────────────────
   8.  aiUploadFile — load a file into a textarea with status display
   ─────────────────────────────────────────────────────────────────────── */

function aiUploadFile(event, textareaId, statusId) {
  var file = event.target.files[0];
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function(e) {
    document.getElementById(textareaId).value = e.target.result;
    var statusEl = document.getElementById(statusId);
    statusEl.textContent = 'Loaded ' + file.name;
    statusEl.className = 'text-sm mt-2 text-success';
  };
  reader.readAsText(file);
  event.target.value = '';
}
