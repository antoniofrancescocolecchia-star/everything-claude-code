#!/usr/bin/env node
// ZHAITAP Tracker JSON Schema Validator
// Runs after writes to tracker/pipeline.json.
// Validates required fields and enumerated values across all records.
// Prints warnings to stderr — does not block (PostToolUse is informational).

'use strict';

const fs = require('fs');
const path = require('path');

const TRACKER_FILENAME = 'tracker/pipeline.json';
const PROJECT_ROOT = process.env.CLAUDE_PLUGIN_ROOT || process.cwd();
const TRACKER_PATH = path.join(PROJECT_ROOT, TRACKER_FILENAME);

// --- Valid value sets ---

const VALID_STATUSES = new Set([
  'not_researched', 'researched', 'draft_prepared',
  'approved_to_use', 'contacted', 'replied', 'blocked', 'archived'
]);

const VALID_DATA_MATURITY = new Set(['verified', 'inferred', 'speculative']);

const VALID_APPROVAL_STATUS = new Set([
  'Pending Antonio review', 'Approved by Antonio', 'Rejected by Antonio'
]);

const VALID_TIERS = new Set([1, 2, 3, '1', '2', '3']);

const VALID_RECORD_TYPES = new Set(['company', 'person', 'institution']);

const VALID_CONTACT_STATUS = new Set([
  'officially confirmed by company source',
  'LinkedIn profile found, consistent with company info',
  'role listed on company website, no direct profile found',
  'unverified lead'
]);

const VALID_CHANNELS = new Set(['email', 'LinkedIn', 'intermediary', 'unknown']);

const REQUIRED_TOP_LEVEL = ['_schema_version', '_description', '_project', 'records'];

const REQUIRED_RECORD_FIELDS = [
  'company', 'country', 'category', 'tier', 'tier_rationale',
  'contacts', 'status', 'source', 'record_type', 'data_maturity',
  'next_action_suggestion', 'approval_status', 'last_updated', 'notes'
];

// --- Main ---

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
  try {
    const input = JSON.parse(raw);
    const { tool_input } = input;

    const filePath = tool_input.file_path || tool_input.path || '';
    if (!filePath.includes(TRACKER_FILENAME)) {
      process.exit(0);
    }

    let tracker;
    try {
      tracker = JSON.parse(fs.readFileSync(TRACKER_PATH, 'utf8'));
    } catch (e) {
      process.stderr.write(
        `[ZHAITAP VALIDATOR] Could not read or parse ${TRACKER_PATH}: ${e.message}\n`
      );
      process.exit(0);
    }

    const warnings = [];

    // Top-level fields
    for (const field of REQUIRED_TOP_LEVEL) {
      if (!(field in tracker)) {
        warnings.push(`Missing top-level field: "${field}"`);
      }
    }

    if (!Array.isArray(tracker.records)) {
      warnings.push('"records" must be an array');
      report(warnings);
      process.exit(0);
    }

    tracker.records.forEach((rec, i) => {
      const label = `Record[${i}] ("${rec.company || 'unnamed'}")`;

      // Required fields presence
      for (const field of REQUIRED_RECORD_FIELDS) {
        if (!(field in rec) || rec[field] === null || rec[field] === undefined) {
          warnings.push(`${label}: missing required field "${field}"`);
        }
      }

      // status
      if (rec.status !== undefined && !VALID_STATUSES.has(rec.status)) {
        warnings.push(`${label}: invalid status "${rec.status}"`);
      }

      // data_maturity
      if (rec.data_maturity !== undefined && !VALID_DATA_MATURITY.has(rec.data_maturity)) {
        warnings.push(`${label}: invalid data_maturity "${rec.data_maturity}"`);
      }

      // approval_status
      if (rec.approval_status !== undefined && !VALID_APPROVAL_STATUS.has(rec.approval_status)) {
        warnings.push(`${label}: invalid approval_status "${rec.approval_status}"`);
      }

      // tier
      if (rec.tier !== undefined && !VALID_TIERS.has(rec.tier)) {
        warnings.push(`${label}: invalid tier "${rec.tier}" — must be 1, 2, or 3`);
      }

      // record_type
      if (rec.record_type !== undefined && !VALID_RECORD_TYPES.has(rec.record_type)) {
        warnings.push(`${label}: invalid record_type "${rec.record_type}" — must be company, person, or institution`);
      }

      // next_action_suggestion must be a non-empty string
      if (rec.next_action_suggestion !== undefined) {
        if (typeof rec.next_action_suggestion !== 'string' || rec.next_action_suggestion.trim() === '') {
          warnings.push(`${label}: next_action_suggestion must be a non-empty string`);
        }
      }

      // contacts array
      if (!Array.isArray(rec.contacts)) {
        warnings.push(`${label}: "contacts" must be an array`);
      } else {
        rec.contacts.forEach((contact, j) => {
          const clabel = `${label} contact[${j}] ("${contact.name || 'unnamed'}")`;

          if (!contact.contact_status) {
            warnings.push(`${clabel}: missing contact_status`);
          } else if (!VALID_CONTACT_STATUS.has(contact.contact_status)) {
            warnings.push(`${clabel}: invalid contact_status "${contact.contact_status}"`);
          }

          if (!contact.channel) {
            warnings.push(`${clabel}: missing channel`);
          } else if (!VALID_CHANNELS.has(contact.channel)) {
            warnings.push(`${clabel}: invalid channel "${contact.channel}" — must be email, LinkedIn, intermediary, or unknown`);
          }
        });
      }
    });

    report(warnings);
    process.exit(0);
  } catch (e) {
    process.stderr.write(`[ZHAITAP VALIDATOR] Unexpected error: ${e.message}\n`);
    process.exit(0);
  }
});

function report(warnings) {
  if (warnings.length > 0) {
    process.stderr.write(
      `[ZHAITAP TRACKER VALIDATOR] ${warnings.length} schema warning(s) in ${TRACKER_FILENAME}:\n` +
      warnings.map(w => `  - ${w}`).join('\n') + '\n'
    );
  }
}
