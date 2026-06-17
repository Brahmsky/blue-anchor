import { createApp } from 'vue';
import 'katex/dist/katex.min.css';

import App from './App.vue';
import router from './router';
import './styles/main.css';

createApp(App).use(router).mount('#app');
