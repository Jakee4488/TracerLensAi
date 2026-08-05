const createDOMPurify = require('dompurify');
const { JSDOM } = require('jsdom');
const window = new JSDOM('').window;
const DOMPurify = createDOMPurify(window);
const html = '<sup class="node-citation" title="Causal Node: X">⚯ X</sup>';
console.log(DOMPurify.sanitize(html));
