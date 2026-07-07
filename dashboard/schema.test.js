import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as schema from './schema.js';

test('conversations() is the conversations root', () => {
	assert.equal(schema.conversations(), 'conversations');
});

test('membersActive(id) targets members_active', () => {
	assert.equal(schema.membersActive('c1'), 'conversations/c1/members_active');
});

test('pendingQuestions(id) targets pending_questions', () => {
	assert.equal(schema.pendingQuestions('c1'), 'conversations/c1/pending_questions');
});

test('agentStatus(id) targets agent_status', () => {
	assert.equal(schema.agentStatus('c1'), 'conversations/c1/agent_status');
});

test('messages(id) targets messages', () => {
	assert.equal(schema.messages('c1'), 'conversations/c1/messages');
});

test('globalAway() targets global_settings/away_mode', () => {
	assert.equal(schema.globalAway(), 'global_settings/away_mode');
});

test('wslAvailable() targets global_settings/wsl_available', () => {
	assert.equal(schema.wslAvailable(), 'global_settings/wsl_available');
});

test('adminNotifications() is the admin_notifications root', () => {
	assert.equal(schema.adminNotifications(), 'admin_notifications');
});

test('widgetRings() targets widget/rings', () => {
	assert.equal(schema.widgetRings(), 'widget/rings');
});

test('widgetQuota() targets widget/quota', () => {
	assert.equal(schema.widgetQuota(), 'widget/quota');
});

test('widgetPushedAt() targets widget/pushed_at', () => {
	assert.equal(schema.widgetPushedAt(), 'widget/pushed_at');
});

test('widgetStatus() targets widget/status', () => {
	assert.equal(schema.widgetStatus(), 'widget/status');
});

test('sessions() is the sessions root', () => {
	assert.equal(schema.sessions(), 'sessions');
});

test('sessionAcks() is the session_acks root', () => {
	assert.equal(schema.sessionAcks(), 'session_acks');
});
