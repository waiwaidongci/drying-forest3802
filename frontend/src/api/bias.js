import {api} from './client.js';

export const biasApi = {
  catalog: () => api('/bias/timers'),
  createTimer: data => api('/bias/timers', {method: 'POST', body: JSON.stringify(data)}),
  updateTimer: (id, data) => api('/bias/timers/' + id, {method: 'PUT', body: JSON.stringify(data)}),
  savePairRule: data => api('/bias/pair-rules', {method: 'POST', body: JSON.stringify(data)}),
  batches: () => api('/bias/batches'),
  specimens: batchKey => api('/bias/batches/' + encodeURIComponent(batchKey) + '/specimens'),
  addSpecimens: data => api('/bias/specimens', {method: 'POST', body: JSON.stringify(data)}),
  analyses: () => api('/bias/analyses'),
  createAnalysis: data => api('/bias/analyses', {method: 'POST', body: JSON.stringify(data)}),
  getAnalysis: id => api('/bias/analyses/' + id),
  recompute: id => api('/bias/analyses/' + id + '/recompute', {method: 'POST'}),
  publish: (id, publish) => api('/bias/analyses/' + id + '/publish', {
    method: 'POST', body: JSON.stringify({publish}),
  }),
  recover: id => api('/bias/analyses/' + id + '/recover', {method: 'POST'}),
};
