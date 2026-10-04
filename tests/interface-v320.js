/* Exercise actual shipped browser script with a minimal DOM. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const events = {};
const values = {};
const makeButton = () => ({attributes: {},span: {textContent: ''},
  setAttribute(name, value) { this.attributes[name] = value; },
  querySelector() { return this.span; },
  addEventListener(name, fn) { this[name] = fn; }});
const theme = makeButton(), menu = makeButton();
const details = [{open: true,inert: false}];
const sidebar = {querySelectorAll: () => details};
const root = {dataset: {},classes: new Set(), classList: {
  toggle(name, enabled) { if (enabled) root.classes.add(name); else root.classes.delete(name); }}};
const storage = {getItem: key => values[key] || null,setItem: (key,value) => {values[key]=value;}};
const media = {matches: false,addEventListener: (name,fn) => {events[name]=fn;}};
const document = {documentElement: root,getElementById: id => ({'ws-theme-toggle':theme,'ws-sidebar-toggle':menu,'ws-sidebar':sidebar})[id]};
const script = fs.readFileSync(path.join(__dirname,'..','agent','static','v320.js'),'utf8');
vm.runInNewContext(script,{document,localStorage:storage,window:{matchMedia:()=>media}});
assert.equal(root.dataset.theme,'light');
assert.equal(menu.attributes['aria-expanded'],'true');
theme.click();
assert.equal(root.dataset.theme,'dark');
assert.equal(values['axiorhub-theme-v320'],'dark');
assert.equal(theme.attributes['aria-label'],'Activer le thème clair');
menu.click();
assert(root.classes.has('ws-collapsed'));
assert.equal(values['axiorhub-sidebar-v320'],'collapsed');
assert.equal(menu.attributes['aria-expanded'],'false');
assert.equal(details[0].inert,true);
assert.equal(details[0].open,false);
menu.click();
assert(!root.classes.has('ws-collapsed'));
assert.equal(details[0].inert,false);
theme.click();
assert.equal(root.dataset.theme,'light');
assert.equal(values['axiorhub-theme-v320'],'light');
