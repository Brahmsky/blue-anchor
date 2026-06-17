import { createRouter, createWebHashHistory } from 'vue-router';

import BenchmarkPage from '@/pages/BenchmarkPage.vue';
import GraphExplorePage from '@/pages/GraphExplorePage.vue';
import HelpPage from '@/pages/HelpPage.vue';
import KnowledgeBasePage from '@/pages/KnowledgeBasePage.vue';
import RagChatPage from '@/pages/RagChatPage.vue';
import SystemConfigPage from '@/pages/SystemConfigPage.vue';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/help' },
    {
      path: '/knowledge-base',
      name: 'knowledge-base',
      component: KnowledgeBasePage,
      meta: {
        title: '知识库管理',
        description: '文档上传、索引状态与入库记录',
        searchPlaceholder: '搜索文档名称、路径…'
      }
    },
    {
      path: '/graph-explore',
      name: 'graph-explore',
      component: GraphExplorePage,
      meta: {
        title: '图谱视图',
        description: '浏览总图、子图与节点关系信息',
        searchPlaceholder: '搜索节点名称、设备或故障…'
      }
    },
    {
      path: '/rag-chat',
      name: 'rag-chat',
      component: RagChatPage,
      meta: {
        title: 'RAG 智能问答',
        description: '语义检索增强生成 · 引用溯源',
        searchPlaceholder: '搜索问题模板、故障现象…'
      }
    },
    {
      path: '/alias-management',
      name: 'alias-management',
      redirect: {
        name: 'system-config',
        query: {
          focus: 'alias-store'
        }
      }
    },
    {
      path: '/benchmark',
      name: 'benchmark',
      component: BenchmarkPage,
      meta: {
        title: 'Benchmark 评测',
        description: '单次 Benchmark 运行观察与结果流入口',
        searchPlaceholder: '搜索题目 ID、题型或运行状态…'
      }
    },
    {
      path: '/system-config',
      name: 'system-config',
      component: SystemConfigPage,
      meta: {
        title: '数据源管理',
        description: '数据源接入状态与 Alias 维护',
        searchPlaceholder: '搜索数据源、目录、Alias…'
      }
    },
    {
      path: '/help',
      name: 'help',
      component: HelpPage,
      meta: {
        title: '帮助文档',
        description: '使用流程与常见问题',
        searchPlaceholder: '搜索上手步骤、常见问题…'
      }
    }
  ]
});

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : '智锚·索引深蓝';
  document.title = `${title} | 智锚·索引深蓝`;
});

export default router;
