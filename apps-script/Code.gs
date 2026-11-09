/**
 * Pipeline Watch mailing list: Google Sheet + Gmail.
 *
 * Deploy as a web app (Execute as: Me, Who has access: Anyone). It does three things:
 *   1. Sign-up:     POST from the web page form (email, phone, consent) -> new row in "Subscribers".
 *   2. Unsubscribe: GET ?action=unsubscribe&token=... (link in every email) -> marks the row.
 *   3. Send:        POST from the daily GitHub job (action=send, secret, subject, body) -> emails
 *                   every active subscriber from your Gmail, each with their own unsubscribe link.
 *
 * Set the shared secret once: Project Settings > Script properties > add SEND_SECRET.
 * Gmail limit: about 100 recipients a day on a free account, 1,500 on Google Workspace.
 */
const SHEET = 'Subscribers';
const HEADERS = ['Signed up', 'Email', 'Phone', 'Status', 'Token', 'Consent'];

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET);
  if (!sh) {
    sh = ss.insertSheet(SHEET);
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
  }
  return sh;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const p = e.parameter || {};
  if (p.action === 'send') return send_(p);
  return signup_(p);
}

function signup_(p) {
  const email = String(p.email || '').trim().toLowerCase();
  const phone = String(p.phone || '').trim().slice(0, 32);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return json_({ ok: false, error: 'invalid email' });
  if (p.consent !== 'yes') return json_({ ok: false, error: 'consent required' });
  const sh = sheet_();
  const rows = sh.getDataRange().getValues();
  for (let i = 1; i < rows.length; i++) {
    if (rows[i][1] === email) {
      sh.getRange(i + 1, 3, 1, 2).setValues([[phone || rows[i][2], 'active']]);
      return json_({ ok: true, existing: true });
    }
  }
  sh.appendRow([new Date(), email, phone, 'active', Utilities.getUuid(), 'yes']);
  return json_({ ok: true });
}

function doGet(e) {
  const p = e.parameter || {};
  if (p.action === 'unsubscribe' && p.token) {
    const sh = sheet_();
    const rows = sh.getDataRange().getValues();
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][4] === p.token) {
        sh.getRange(i + 1, 4).setValue('unsubscribed');
        return HtmlService.createHtmlOutput('<p style="font-family:sans-serif">You are unsubscribed from Pipeline Watch emails.</p>');
      }
    }
    return HtmlService.createHtmlOutput('<p style="font-family:sans-serif">That unsubscribe link was not found.</p>');
  }
  return HtmlService.createHtmlOutput('<p style="font-family:sans-serif">Pipeline Watch mailing list is running.</p>');
}

function send_(p) {
  const secret = PropertiesService.getScriptProperties().getProperty('SEND_SECRET');
  if (!secret || p.secret !== secret) return json_({ ok: false, error: 'unauthorized' });
  const subject = String(p.subject || 'Pipeline Watch update');
  const body = String(p.body || '');
  if (!body) return json_({ ok: false, error: 'empty body' });
  const base = ScriptApp.getService().getUrl();
  const rows = sheet_().getDataRange().getValues();
  let sent = 0, skipped = 0;
  for (let i = 1; i < rows.length; i++) {
    const [, email, , status, token] = rows[i];
    if (status !== 'active') continue;
    if (MailApp.getRemainingDailyQuota() < 1) { skipped++; continue; }
    const unsub = base + '?action=unsubscribe&token=' + encodeURIComponent(token);
    MailApp.sendEmail({
      to: email,
      subject: subject,
      body: body + '\n\nUnsubscribe: ' + unsub,
      name: 'Pipeline Watch',
    });
    sent++;
  }
  return json_({ ok: true, sent: sent, skipped_over_quota: skipped });
}
