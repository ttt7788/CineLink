const useStrm = (API_BASE, ElMessage, msgBox) => {
    const { ref } = Vue;

    const strmConfigs = ref([]);
    const showStrmDialog = ref(false);
    const isEditingConfig = ref(false);
    const editingConfigId = ref(null);
    const defaultStrmConfig = () => ({ source_type: 'webdav', config_name: '', url: '', username: '', password: '', rootpath: '', target_directory: '', update_mode: 'incremental', download_enabled: 1, download_interval_range: '1-3' });
    const newStrmConfig = ref(defaultStrmConfig());

    const strmRecords = ref([]);
    const recordTotal = ref(0);
    const recordPage = ref(1);
    const recordPageSize = ref(20);
    const recordSummaries = ref([]);
    const activeRecordConfigId = ref('all');

    const strmTasks = ref([]);
    const showTaskDialog = ref(false);
    const isEditingTask = ref(false);
    const editingTaskId = ref(null);
    const newStrmTask = ref({ task_name: '', config_id: null, cron_expression: '0 */2 * * *', is_enabled: 1 });

    const strmSettings = ref({ video_formats: '', subtitle_formats: '', image_formats: '', metadata_formats: '', size_threshold: 100, download_threads: 4 });
    const replaceTool = ref({ target_directory: '', old_domain: '', new_domain: '' });

    const loadStrmConfigs = async () => { const r = await axios.get(`${API_BASE}/strm/configs`); strmConfigs.value = r.data; };
    const loadStrmSettings = async () => { const r = await axios.get(`${API_BASE}/strm/settings`); strmSettings.value = r.data; };
    const loadStrmTasks = async () => { const r = await axios.get(`${API_BASE}/strm/tasks`); strmTasks.value = r.data; };
    const getStrmConfigName = (id) => { const c = strmConfigs.value.find(x => x.id === id); return c ? c.config_name : '未知节点'; };

    const applyStrmSourceType = () => {
        const internalSources = {
            '115_internal': { rootpath: '/115', name: '115网盘内置WebDAV' },
            'aliyun_internal': { rootpath: '/aliyun', name: '阿里云盘内置WebDAV' },
            'quark_internal': { rootpath: '/quark', name: '夸克网盘内置WebDAV' },
        };
        const source = internalSources[newStrmConfig.value.source_type];
        if (source) {
            newStrmConfig.value.url = 'http://127.0.0.1:8088';
            newStrmConfig.value.rootpath = source.rootpath;
            newStrmConfig.value.username = '';
            newStrmConfig.value.password = '';
            if (!newStrmConfig.value.config_name) newStrmConfig.value.config_name = source.name;
        }
    };
    const openStrmDialog = () => { isEditingConfig.value = false; newStrmConfig.value = defaultStrmConfig(); showStrmDialog.value = true; };
    const editStrmConfig = (row) => { isEditingConfig.value = true; editingConfigId.value = row.id; newStrmConfig.value = { source_type: 'webdav', ...row }; applyStrmSourceType(); showStrmDialog.value = true; };
    const saveStrmConfig = async () => {
        try {
            applyStrmSourceType();
            if (isEditingConfig.value) { await axios.put(`${API_BASE}/strm/configs/${editingConfigId.value}`, newStrmConfig.value); }
            else { await axios.post(`${API_BASE}/strm/configs`, newStrmConfig.value); }
            ElMessage.success('操作成功'); showStrmDialog.value = false; loadStrmConfigs();
        } catch (err) { ElMessage.error('操作失败'); }
    };
    const deleteStrmConfig = async (id) => { try { await msgBox.confirm('确定删除?'); await axios.delete(`${API_BASE}/strm/configs/${id}`); loadStrmConfigs(); } catch (e) {} };
    const runStrmTask = async (id) => { try { await axios.post(`${API_BASE}/strm/run/${id}`); ElMessage.success('生成任务已投递至后台，请查看日志！'); } catch (e) {} };

    const getRecordSummaryCount = (id) => {
        if (id === 'all') return recordSummaries.value.reduce((total, item) => total + Number(item.record_count || 0), 0);
        const item = recordSummaries.value.find(x => String(x.id) === String(id));
        return item ? Number(item.record_count || 0) : 0;
    };
    const handleRecordTabChange = () => { recordPage.value = 1; loadStrmRecords(); };
    const loadStrmRecords = async () => {
        const params = new URLSearchParams({ page: recordPage.value, size: recordPageSize.value });
        if (activeRecordConfigId.value !== 'all') params.set('config_id', activeRecordConfigId.value);
        const r = await axios.get(`${API_BASE}/strm/records?${params.toString()}`);
        strmRecords.value = r.data.items;
        recordTotal.value = r.data.total;
        recordSummaries.value = r.data.summaries || [];
    };
    const clearStrmRecords = async () => {
        try {
            const isAll = activeRecordConfigId.value === 'all';
            const target = isAll ? '全部节点' : getStrmConfigName(Number(activeRecordConfigId.value));
            await msgBox.confirm(`确定清空 [${target}] 的 STRM 生成记录缓存？下次生成会重新扫描比对。`, '警告', { type: 'danger' });
            const url = isAll ? `${API_BASE}/strm/records/clear` : `${API_BASE}/strm/records/clear?config_id=${activeRecordConfigId.value}`;
            await axios.delete(url);
            ElMessage.success('记录已清空');
            loadStrmRecords();
        } catch (e) {}
    };

    const openTaskDialog = () => { isEditingTask.value = false; editingTaskId.value = null; newStrmTask.value = { task_name: '', config_id: null, cron_expression: '0 */2 * * *', is_enabled: 1 }; showTaskDialog.value = true; };
    const editStrmTask = (row) => { isEditingTask.value = true; editingTaskId.value = row.id; newStrmTask.value = { task_name: row.task_name, config_id: row.config_id, cron_expression: row.cron_expression, is_enabled: row.is_enabled }; showTaskDialog.value = true; };
    const saveStrmTask = async () => {
        if(!newStrmTask.value.config_id || !newStrmTask.value.task_name) return ElMessage.warning('请填写完整');
        try { 
            if (isEditingTask.value) { await axios.put(`${API_BASE}/strm/tasks/${editingTaskId.value}`, newStrmTask.value); } 
            else { await axios.post(`${API_BASE}/strm/tasks`, newStrmTask.value); }
            ElMessage.success('保存成功'); showTaskDialog.value = false; loadStrmTasks(); 
        } catch (e) { ElMessage.error('操作失败'); }
    };
    const toggleTaskStatus = async (row) => { try { await axios.post(`${API_BASE}/strm/tasks/status`, { id: row.id, is_enabled: row.is_enabled }); ElMessage.success('状态已更新'); } catch (e) {} };
    const deleteStrmTask = async (id) => { try { await msgBox.confirm('确定删除?'); await axios.delete(`${API_BASE}/strm/tasks/${id}`); loadStrmTasks(); } catch (e) {} };

    const saveStrmSettings = async () => { try { await axios.post(`${API_BASE}/strm/settings`, strmSettings.value); ElMessage.success('规则保存成功'); } catch (e) {} };
    const runReplaceDomain = async () => { if (!replaceTool.value.target_directory || !replaceTool.value.old_domain || !replaceTool.value.new_domain) return ElMessage.warning('参数不全'); try { await axios.post(`${API_BASE}/strm/replace_domain`, replaceTool.value); ElMessage.success('后台替换中！'); } catch (e) {} };

    return {
        strmConfigs, showStrmDialog, isEditingConfig, newStrmConfig,
        strmRecords, recordTotal, recordPage, recordPageSize, recordSummaries, activeRecordConfigId,
        strmTasks, showTaskDialog, newStrmTask, isEditingTask,
        strmSettings, replaceTool,
        loadStrmConfigs, openStrmDialog, editStrmConfig, saveStrmConfig, deleteStrmConfig, runStrmTask, applyStrmSourceType,
        loadStrmRecords, clearStrmRecords, handleRecordTabChange, getRecordSummaryCount,
        loadStrmTasks, openTaskDialog, editStrmTask, saveStrmTask, toggleTaskStatus, deleteStrmTask, getStrmConfigName,
        loadStrmSettings, saveStrmSettings, runReplaceDomain
    };
};
window.useStrm = useStrm;
