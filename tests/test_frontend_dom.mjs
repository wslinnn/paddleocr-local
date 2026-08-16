// DOM-level frontend tests: boot static/index.html + i18n.js + app.js inside
// jsdom with stubbed fetch/vendor globals, then drive real DOM events.
//
// Adapted from upstream CHEN010325/paddleocr-local. Upstream's 28
// coverage-chasing tests target its own app.js; this fork keeps the harness
// plus the boot smoke test, the connection-failure path, and coverage for
// this fork's backend-owned task creation flow.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';


const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(rootDir, 'static', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(rootDir, 'static', 'app.js'), 'utf8');
const i18n = fs.readFileSync(path.join(rootDir, 'static', 'i18n.js'), 'utf8');


function jsonResponse(data, status = 200) {
    return new Response(JSON.stringify(data), {
        status,
        headers: { 'content-type': 'application/json' }
    });
}


function createBrowser(fetchOverride = null, withI18n = true) {
    const dom = new JSDOM(html, {
        runScripts: 'outside-only',
        url: 'http://localhost:8000/'
    });
    const { window } = dom;
    window.Response = Response;
    window.Request = Request;
    window.Headers = Headers;
    window.TextDecoder = TextDecoder;
    window.TextEncoder = TextEncoder;
    window.URL.createObjectURL = () => 'blob:test';
    window.URL.revokeObjectURL = () => {};
    window.alert = () => {};
    window.confirm = () => true;
    window.prompt = () => 'transformers';
    window.scrollTo = () => {};
    window.HTMLElement.prototype.scrollIntoView = function () {};
    window.HTMLElement.prototype.scrollTo = function (options = {}) {
        if (typeof options === 'object') {
            if (Number.isFinite(options.top)) this.scrollTop = options.top;
            if (Number.isFinite(options.left)) this.scrollLeft = options.left;
        }
    };
    window.HTMLElement.prototype.getBoundingClientRect = function () {
        return { top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100 };
    };
    // jsdom never loads images, so make decode() resolve to keep
    // waitForImageReady from awaiting a load event that will not fire.
    window.HTMLImageElement.prototype.decode = () => Promise.resolve();
    window.HTMLCanvasElement.prototype.getContext = () => ({});
    window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,AA==';
    window.requestAnimationFrame = (callback) => {
        callback();
        return 1;
    };
    window.cancelAnimationFrame = () => {};
    window.setInterval = () => 1;
    window.clearInterval = () => {};
    window.navigator.clipboard = { writeText: async () => {} };
    window.document.execCommand = () => true;
    window.pdfjsLib = {
        GlobalWorkerOptions: {},
        getDocument: () => ({ promise: Promise.resolve({ numPages: 1 }) })
    };
    window.PDFLib = {
        PDFDocument: {
            load: async () => ({
                copyPages: async () => [],
                addPage() {},
                save: async () => new Uint8Array([1])
            }),
            create: async () => ({
                copyPages: async () => [],
                addPage() {},
                save: async () => new Uint8Array([1])
            })
        }
    };
    window.marked = { parse: (value) => `<p>${value}</p>` };
    window.DOMPurify = { sanitize: (value) => value };
    window.hljs = { highlightElement() {} };
    window.renderMathInElement = () => {};
    window.JSZip = class {
        file() {}
        folder() { return this; }
        async generateAsync() { return new Blob(['zip']); }
    };

    const runtime = {
        controlAvailable: true,
        activeModelId: 'paddleocr-vl-1.6',
        unlimitedOcrBackend: 'transformers',
        models: {
            'paddleocr-vl-1.6': { ready: true, state: 'running' },
            'unlimited-ocr': {
                ready: false,
                state: 'stopped',
                unlimitedOcrSupportedBackends: ['transformers', 'sglang']
            },
            ovisocr2: { ready: false, state: 'missing', available: false }
        }
    };
    const task = {
        id: 'task-1',
        name: 'sample.png',
        originalName: 'sample.png',
        sourceKind: 'image',
        sourceUrl: '/api/tasks/task-1/source',
        size: 100,
        pageCount: 1,
        modelId: 'paddleocr-vl-1.6',
        modelName: 'PaddleOCR-VL',
        modelEndpoint: '/api/paddleocr-vl-1.6',
        status: 'completed',
        updatedAt: 2,
        markdown: '# Result',
        images: {},
        ocrResults: [{ markdown: { text: '# Result', images: {} }, parsing_res_list: [] }],
        batches: [{ id: 'batch-1', status: 'completed', startPage: 1, endPage: 1, pageCount: 1 }]
    };
    const fetch = fetchOverride || (async (url, options = {}) => {
        const pathname = new URL(String(url), window.location.href).pathname;
        if (pathname === '/api/models') {
            return jsonResponse({
                default: 'paddleocr-vl-1.6',
                maxUploadBytes: 1024,
                data: [
                    { id: 'paddleocr-vl-1.6', label: 'PaddleOCR', endpoint: '/api/paddleocr-vl-1.6' },
                    { id: 'unlimited-ocr', label: 'Unlimited OCR', endpoint: '/api/unlimited-ocr' },
                    { id: 'ovisocr2', label: 'OvisOCR2', endpoint: '/api/ovisocr2' }
                ]
            });
        }
        if (pathname === '/api/model-runtime') return jsonResponse(runtime);
        if (pathname === '/api/tasks') return jsonResponse({ tasks: [task] });
        if (pathname === '/api/tasks/task-1') return jsonResponse(task);
        if (pathname === '/api/tasks/task-1/source') return new Response(new Uint8Array([1, 2]), { status: 200 });
        if (pathname.includes('/model-runtime/') || pathname.includes('/unlimited-ocr/backend')) return jsonResponse(runtime);
        if (options.method === 'DELETE' || options.method === 'PUT' || options.method === 'POST') return jsonResponse({});
        return jsonResponse({}, 404);
    });
    window.fetch = fetch;
    if (withI18n) {
        new vm.Script(i18n, { filename: path.join(rootDir, 'static', 'i18n.js') })
            .runInContext(dom.getInternalVMContext());
    }
    new vm.Script(app, { filename: path.join(rootDir, 'static', 'app.js') })
        .runInContext(dom.getInternalVMContext());
    return { dom, window, runtime, task };
}


async function boot(window) {
    window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
    await new Promise((resolve) => window.setTimeout(resolve, 10));
}


test('full DOM boot renders models, tasks, language, tabs, and controls', async () => {
    const { dom, window } = createBrowser();
    try {
        await boot(window);
        assert.equal(window.document.querySelectorAll('#model-select option').length, 3);
        assert.equal(window.document.querySelectorAll('.task-item').length, 1);

        window.document.getElementById('language-toggle').click();
        window.document.getElementById('language-toggle').click();
        window.document.getElementById('sidebar-toggle').click();
        window.document.querySelector('[data-view="json"]').click();
        window.document.querySelector('[data-view="markdown"]').click();
        window.document.querySelector('[data-filter="done"]').click();
        window.document.querySelector('[data-filter="all"]').click();
        window.document.getElementById('task-search').value = 'sample';
        window.document.getElementById('task-search').dispatchEvent(new window.Event('input'));

        const batch = window.document.getElementById('pdf-batch-size-input');
        batch.value = '999';
        batch.dispatchEvent(new window.Event('input'));
        assert.equal(batch.value, '400');
        batch.value = '';
        batch.dispatchEvent(new window.Event('input'));
        batch.value = '2';
        batch.dispatchEvent(new window.Event('change'));

        for (const name of ['dragenter', 'dragover', 'dragleave']) {
            window.document.dispatchEvent(new window.Event(name, { bubbles: true, cancelable: true }));
        }
        window.document.getElementById('prev-page-btn').click();
        window.document.getElementById('next-page-btn').click();
        window.document.getElementById('zoom-in-btn').click();
        window.document.getElementById('zoom-out-btn').click();
        window.document.getElementById('reset-zoom-btn').click();
    } finally {
        dom.window.close();
    }
});


test('backend boot failure updates connection status and schedules retry', async () => {
    const { dom, window } = createBrowser(async () => new Response('down', { status: 500 }));
    let retry = null;
    window.setTimeout = (callback) => {
        retry = callback;
        return 1;
    };
    try {
        window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
        await new Promise((resolve) => globalThis.setTimeout(resolve, 10));
        assert.equal(window.document.getElementById('model-status-dot').className, 'dot error');
        assert.equal(typeof retry, 'function');
    } finally {
        dom.window.close();
    }
});


test('task creation posts intent to the backend-owned endpoint', async () => {
    const calls = [];
    // jsdom fires DOMContentLoaded on its own once parsing finishes, so the
    // app's boot sequence runs alongside this test — serve its requests too,
    // otherwise checkBackendFailure schedules a real 5s retry timer.
    const { dom, window } = createBrowser(async (url, options = {}) => {
        const pathname = new URL(String(url), window.location.href).pathname;
        calls.push([options.method || 'GET', pathname]);
        if (pathname === '/api/models') {
            return jsonResponse({
                default: 'paddleocr-vl-1.6',
                data: [{ id: 'paddleocr-vl-1.6', label: 'PaddleOCR', endpoint: '/api/paddleocr-vl-1.6' }]
            });
        }
        if (pathname === '/api/model-runtime') {
            return jsonResponse({ controlAvailable: true, activeModelId: 'paddleocr-vl-1.6', models: {} });
        }
        if (pathname === '/api/tasks' && options.method === 'POST') {
            return jsonResponse({
                id: 'created-1',
                name: 'pic.png',
                sourceKind: 'image',
                status: 'pending',
                pageCount: 1,
                sourceUrl: '/api/tasks/created-1/source',
                modelId: 'pp-ocrv6-rapid',
                modelName: 'PP-OCRv6 (RapidOCR·CPU)',
                batches: [{ id: 'b1', fileType: 1, startPage: 1, endPage: 1, pageCount: 1, status: 'pending' }]
            }, 201);
        }
        return jsonResponse({});
    });

    try {
        // Let the jsdom-triggered boot chain settle first — otherwise it keeps
        // issuing fetches after the test ends (and after window.close()).
        await boot(window);
        const file = new window.File([new Uint8Array([1, 2, 3])], 'pic.png', { type: 'image/png' });
        const task = await window.createTaskOnServer(file, { sourceKind: 'image', modelId: 'pp-ocrv6-rapid' });

        assert.equal(task.id, 'created-1');
        assert.equal(task.detailLoaded, true);
        assert.equal(task.batches.length, 1);
        assert.ok(
            calls.some(([method, pathname]) => method === 'POST' && pathname === '/api/tasks'),
            'creation must hit POST /api/tasks'
        );
    } finally {
        dom.window.close();
    }
});
