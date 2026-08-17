/*
 * Modifications Copyright (c) 2026 wslinnn
 * This file has been modified from the upstream project
 * https://github.com/CHEN010325/paddleocr-local (Apache-2.0).
 */

// Frontend logic tests: static/app.js runs in a bare VM context (no DOM
// events fire — only pure functions are exercised here). DOM-level behaviour
// lives in test_frontend_dom.mjs.
//
// Adapted from upstream CHEN010325/paddleocr-local: tests for HPD-Parsing and
// browser-side PDF batch planning were dropped (this fork moved batch
// planning to the backend; see server.build_batch_plan and its Python tests).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';


const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const appPath = path.join(rootDir, 'static', 'app.js');
const storage = new Map();
const localStorage = {
    getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
        storage.set(key, String(value));
    },
    removeItem(key) {
        storage.delete(key);
    },
    clear() {
        storage.clear();
    }
};
const document = {
    addEventListener() {},
    getElementById() {
        return null;
    },
    querySelector() {
        return null;
    },
    body: {},
    documentElement: {},
    title: 'PaddleOCR Local'
};
const window = {
    sessionStorage: localStorage,
    PANDOCR_I18N: {
        defaultLanguage: 'zh-CN',
        supportedLanguages: ['zh-CN', 'en'],
        titles: {
            'zh-CN': 'PaddleOCR Local',
            en: 'PaddleOCR Local'
        },
        dictionaries: {
            en: {}
        }
    },
    location: {
        href: 'http://localhost:8000/',
        origin: 'http://localhost:8000'
    }
};
const context = vm.createContext({
    Blob,
    Headers,
    URL,
    Uint8Array,
    atob,
    btoa,
    clearTimeout,
    console,
    document,
    fetch: async () => {
        throw new Error('Unexpected network access from frontend unit test');
    },
    localStorage,
    setTimeout,
    window
});
vm.runInContext(fs.readFileSync(appPath, 'utf8'), context, { filename: appPath });


function evaluate(expression) {
    return vm.runInContext(expression, context);
}


function plain(value) {
    return JSON.parse(JSON.stringify(value));
}


test.beforeEach(() => {
    storage.clear();
    evaluate("currentLanguage = 'zh-CN'");
});


test('language normalization and interpolation use safe fallbacks', () => {
    assert.equal(evaluate("normalizeLanguage('en')"), 'en');
    assert.equal(evaluate("normalizeLanguage('fr')"), 'zh-CN');
    assert.equal(
        evaluate("interpolateI18n('Page {page} of {total}', { page: 2 })"),
        'Page 2 of {total}'
    );
    assert.equal(evaluate("hasCjk('中文')"), true);
    assert.equal(evaluate("hasCjk('plain text')"), false);
});


test('API authentication is attached only to same-origin API URLs', () => {
    window.sessionStorage.setItem('pandocr.apiToken', 'secret-token');

    assert.equal(evaluate("isLocalApiUrl('/api/models')"), true);
    assert.equal(evaluate("isLocalApiUrl('http://localhost:8000/api/tasks')"), true);
    assert.equal(evaluate("isLocalApiUrl('https://example.com/api/tasks')"), false);
    assert.equal(
        evaluate("authHeaders({}, '/api/models').get('authorization')"),
        'Bearer secret-token'
    );
    assert.equal(
        evaluate("authHeaders({}, 'https://example.com/api/models').get('authorization')"),
        null
    );
});


test('model and task normalization preserve the newest meaningful state', () => {
    const models = plain(evaluate(`normalizeModelList({
        data: [
            'legacy',
            { id: 'pp-ocrv6', label: 'PP OCR', endpoint: '/pp-ocrv6' }
        ]
    })`));
    assert.equal(models[0].id, 'legacy');
    assert.equal(models[1].endpoint, '/pp-ocrv6');
    assert.equal(evaluate("normalizeUnlimitedOcrBackend('SGLANG')"), 'sglang');
    assert.equal(evaluate("normalizeUnlimitedOcrBackend('invalid')"), 'transformers');

    // This fork dedupes by task id (distinct uploads of the same file are
    // distinct tasks); the newest version of an id wins.
    const tasks = plain(evaluate(`dedupeTasks([
        { id: 'same', name: 'a.pdf', size: 10, pageCount: 1, updatedAt: 1 },
        { id: 'same', name: 'a.pdf', size: 12, pageCount: 1, updatedAt: 4 },
        { id: 'other', name: 'b.pdf', size: 10, pageCount: 1, updatedAt: 3 }
    ])`));
    assert.deepEqual(tasks.map((task) => task.id), ['same', 'other']);
    assert.equal(tasks[0].size, 12);

    const completed = plain(evaluate(`reconcileTaskStatus({
        id: 'task',
        status: 'processing',
        sourceUrl: '/api/tasks/task/source',
        batches: [{ status: 'completed' }],
        ocrResults: [{}]
    })`));
    assert.equal(completed.status, 'completed');
});


test('configured PDF batch size is clamped to the supported range', () => {
    assert.equal(evaluate("clampPdfBatchSize('0')"), 1);
    assert.equal(evaluate("clampPdfBatchSize('999')"), 400);
    assert.equal(evaluate("clampPdfBatchSize('invalid')"), 1);
    assert.equal(evaluate("clampPdfBatchSize('2')"), 2);
    // Batch planning itself lives in the backend now (server.build_batch_plan);
    // here we only guard the client-side preference plumbing.
    evaluate("localStorage.setItem('pandocr.pdfBatchSize', '3')");
    assert.equal(evaluate("clampPdfBatchSize(localStorage.getItem('pandocr.pdfBatchSize'))"), 3);
});


test('task persistence strips transient payloads and status placeholders', () => {
    const metadataOnly = plain(evaluate(`taskForPersistence({
        id: 'task',
        sourceUrl: '/api/tasks/task/source',
        sourceDataUrl: 'data:application/pdf;base64,AA==',
        markdown: 'result',
        images: { image: 'base64' },
        ocrResults: [{}],
        batches: [{
            id: 'batch',
            markdown: 'batch result',
            payloadDataUrl: 'data:application/pdf;base64,AA==',
            payloadBlob: { size: 10 },
            _streamStatus: 'loading'
        }]
    }, { includeResults: false })`));

    assert.equal(metadataOnly._preserveResult, true);
    assert.equal('sourceDataUrl' in metadataOnly, false);
    assert.equal('markdown' in metadataOnly, false);
    assert.equal('images' in metadataOnly, false);
    assert.equal('ocrResults' in metadataOnly, false);
    assert.equal('payloadDataUrl' in metadataOnly.batches[0], false);
    assert.equal('payloadBlob' in metadataOnly.batches[0], false);
    assert.equal('markdown' in metadataOnly.batches[0], false);

    assert.equal(
        evaluate(`stripStreamStatusMarkdown(
            'Final text\\n\\n**Unlimited-OCR status**\\n\\nLoading model'
        )`),
        'Final text'
    );
});


test('stream events and normalized coordinates are validated defensively', () => {
    assert.deepEqual(
        plain(evaluate(`parseStreamingOCREvent('{"type":"progress","page":2}')`)),
        { type: 'progress', page: 2 }
    );
    assert.equal(evaluate("parseStreamingOCREvent('not-json')"), null);

    const position = plain(evaluate(`streamingSourcePosition({
        source: {
            pageIndex: 8,
            pageProgress: 2,
            bbox: [10, 20, 30, 40],
            pageWidth: 1024,
            pageHeight: 1024,
            label: 'text'
        }
    }, { startPage: 3, endPage: 4, pageCount: 2 })`));
    assert.equal(position.pageNumber, 4);
    assert.equal(position.pageProgress, 1);
    assert.equal(position.pageWidth, 1000);
    assert.equal(position.pageHeight, 1000);
    assert.equal(
        evaluate("streamingSourcePosition({ source: { bbox: ['bad'] } }, {})"),
        null
    );
});


test('OCR markdown and result compaction remove transport-only data', () => {
    const markdown = evaluate(`cleanUnlimitedOCRMarkdown(
        '<|det|>header [1,2,3,4]<|/det|>skip ' +
        '<|det|>title [1,2,3,4]<|/det|>Title ' +
        '<|det|>formula [1,2,3,4]<|/det|>x^2'
    )`);
    assert.equal(markdown.includes('skip'), false);
    assert.equal(markdown.includes('# Title'), true);
    assert.equal(markdown.includes('$$\nx^2\n$$'), true);

    const prepared = plain(evaluate(`prepareBatchResult({
        markdown: '![figure](images/figure.jpg)',
        images: { 'images/figure.jpg': 'base64-image' }
    }, 'batch-1')`));
    assert.equal(
        prepared.markdown,
        '![figure](ocr_images/batch-1_figure.jpg)'
    );
    assert.deepEqual(
        prepared.images,
        { 'ocr_images/batch-1_figure.jpg': 'base64-image' }
    );

    // inputImage is kept on purpose: the pp-ocrv6 / rapidocr visual layer
    // renders the faded source image from it. Only outputImages is stripped.
    const compact = plain(evaluate(`stripLargeOCRFields({
        inputImage: 'kept',
        nested: { outputImages: ['large'], keep: 1 }
    })`));
    assert.deepEqual(compact, { inputImage: 'kept', nested: { keep: 1 } });
});


test('page image references pass through as URLs or data URLs', () => {
    // Backend-owned page images arrive as /api/... URLs (P3 refactor); raw
    // base64 payloads and data URLs keep working for legacy inline storage.
    assert.equal(
        evaluate("imageValueToSrc('/api/tasks/t1/pages/p0001.jpg')"),
        '/api/tasks/t1/pages/p0001.jpg'
    );
    assert.equal(
        evaluate("imageValueToSrc('data:image/jpeg;base64,AA==')"),
        'data:image/jpeg;base64,AA=='
    );
    assert.equal(
        evaluate("imageValueToSrc('SGVsbG8=')"),
        'data:image/jpeg;base64,SGVsbG8='
    );
    assert.equal(
        evaluate("imageValueToSrc('ocr_images/a.jpg')"),
        'ocr_images/a.jpg'
    );
});


test('replaceTask treats the server document as authoritative', () => {
    evaluate("tasks = []");
    const first = plain(evaluate(`replaceTask({
        id: 't1', name: 'old.pdf', markdown: 'stale', thumbnail: 'thumb', pageCount: 2
    })`));
    assert.equal(first.detailLoaded, true);
    evaluate("tasks[0].jobState = 'queued'");

    const replaced = plain(evaluate(`replaceTask({
        id: 't1', name: 'old.pdf', pageCount: 2
    })`));
    // Server-dropped fields do not linger; session-only fields carry over.
    assert.equal('markdown' in replaced, false);
    assert.equal('thumbnail' in replaced, false);
    assert.equal(replaced.jobState, 'queued');
    assert.equal(replaced.detailLoaded, true);
    // A field the server still sends survives replacement.
    assert.equal(replaced.pageCount, 2);

    const added = plain(evaluate(`replaceTask({ id: 't2', name: 'new.pdf' })`));
    assert.equal(added.id, 't2');
    assert.equal(plain(evaluate('tasks.length')), 2);
    evaluate("tasks = []");
});


test('binary and filename helpers produce safe deterministic values', () => {
    assert.deepEqual(
        plain(evaluate("Array.from(dataUrlToUint8Array('data:text/plain;base64,SGk='))")),
        [72, 105]
    );
    assert.deepEqual(
        plain(evaluate("Array.from(base64ToBytes('SGk='))")),
        [72, 105]
    );
    assert.equal(
        evaluate(`safeDownloadName('bad:name?.pdf', 'md')`),
        'bad_name_.md'
    );
    assert.equal(
        evaluate("batchDisplayLabel({ startPage: 3, endPage: 5 })"),
        '第 3-5 页'
    );
    assert.equal(
        evaluate("batchDisplayLabel({ startPage: 2, endPage: 2 })"),
        '第 2 页'
    );
});
