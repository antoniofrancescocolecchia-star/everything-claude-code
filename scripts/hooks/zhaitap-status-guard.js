#!/usr/bin/env node
// ZHAITAP Status Mutation Guard
// Reads current tracker state, compares per-record transitions.
// Blocks only if a record is newly moving INTO a protected status.
// Does not block if the protected status already existed and is unchanged.

'use strict';

const fs = require('fs');
const path = require('path');

const PROTECTED_STATUSES = new Set(['approved_to_use', 'contacted', 'replied']);
const TRACKER_FILENAME = 'tracker/pipeline.json';

// Derive project root: prefer CLAUDE_PLUGIN_ROOT env, fall back to cwd
const PROJECT_ROOT = process.env.CLAUDE_PLUGIN_ROOT || process.cwd();
const TRACKER_PATH = path.join(PROJECT_ROOT, TRACKER_FILENAME);

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
  try {
    const input = JSON.parse(raw);
    const { tool_input } = input;

    // Only act on writes targeting tracker/pipeline.json
    const filePath = tool_input.file_path || tool_input.path || '';
    if (!filePath.includes(TRACKER_FILENAME)) {
      process.exit(0);
    }

    // Parse the proposed content
    const proposedRaw = tool_input.content || tool_input.new_string || '';
    let proposed;
    try {
      proposed = JSON.parse(proposedRaw);
    } catch (_) {
      // Malformed JSON — let schema validator handle it, do not block here
      process.exit(0);
    }

    const proposedRecords = proposed.records;
    if (!Array.isArray(proposedRecords) || proposedRecords.length === 0) {
      process.exit(0);
    }

    // Read current tracker state from disk
    let currentRecords = [];
    if (fs.existsSync(TRACKER_PATH)) {
      try {
        const current = JSON.parse(fs.readFileSync(TRACKER_PATH, 'utf8'));
        currentRecords = Array.isArray(current.records) ? current.records : [];
      } catch (_) {
        // Could not read current state — treat all records as new
        currentRecords = [];
      }
    }

    // Build lookup: company name (normalized) → current status
    const currentStatusByCompany = new Map();
    for (const rec of currentRecords) {
      if (rec.company) {
        currentStatusByCompany.set(rec.company.trim().toLowerCase(), rec.status);
      }
    }

    // Check each proposed record for protected status transitions
    const blocked = [];
    for (const rec of proposedRecords) {
      const proposedStatus = rec.status;
      if (!PROTECTED_STATUSES.has(proposedStatus)) continue;

      const key = (rec.company || '').trim().toLowerCase();
      const currentStatus = currentStatusByCompany.get(key);

      if (currentStatus === proposedStatus) {
        // Status unchanged — not a new transition, allow
        continue;
      }

      // New record starting at a protected status, or transitioning into one
      blocked.push({
        company: rec.company || '(unnamed)',
        from: currentStatus || '(new record)',
        to: proposedStatus
      });
    }

    if (blocked.length > 0) {
      const detail = blocked
        .map(b => `  "${b.company}": ${b.from} → ${b.to}`)
        .join('\n');
      const result = {
        decision: 'block',
        reason:
          `ZHAITAP STATUS GUARD: The following records are transitioning to a protected status without Antonio's explicit confirmation:\n${detail}\n\n` +
          `Protected statuses (approved_to_use, contacted, replied) can only be written after Antonio explicitly confirms the action occurred. ` +
          `Please confirm with Antonio first, then instruct the Tracker agent to write the update.`
      };
      process.stdout.write(JSON.stringify(result));
      process.exit(0);
    }

    process.exit(0);
  } catch (e) {
    // Unexpected error — do not block
    process.exit(0);
  }
});
