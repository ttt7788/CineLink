const { createApp, ref, onMounted, watch } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;
const msgBox = ElMessageBox;
const API_BASE = '/api';

const app = createApp({
    setup() {
        const activeMenu = ref('hot'), syncingData = ref(false), loading = ref(false);
        const lm = ref([]), sr = ref([]), sq = ref('');
        const subscriptions = ref([]), records = ref([]), systemLogs = ref([]);
        const currentPage = ref(1), pageSize = ref(30), totalItems = ref(0);
        
        const selectedMediaList = ref([]);
        const selectedTableRows = ref([]);
        
        const driveFiles = ref([]);
        const driveLoading = ref(false);
        const drivePaths = ref([]); 
        const currentDriveType = ref(''); 
        
        // 【核心修改】新增 auto_subscribe_drive 数据项，默认值 115
        const config = ref({ api_domain: '', image_domain: '', api_key: '', pansou_domain: '', cookie_115: '', cookie_quark: '', token_aliyun: '', quark_save_dir: '0', aliyun_save_dir: 'root', cron_expression: '', cms_api_url: '', cms_api_token: '', auto_subscribe_new: '0', auto_subscribe_drive: '115' });
        
        const pv = ref(false), pr = ref({}), curKw = ref('');
        const curMedia = ref(null), savingLink = ref(false); 
        const qrLoading = ref(false), qUrl = ref(''), qSt = ref(''), qTok = ref(null), pTimer = ref(null);

        const autoRefreshLogs = ref(true);
        const logTimer = ref(null);

        const startLogPoll = () => {
            if (logTimer.value) clearInterval(logTimer.value);
            if (autoRefreshLogs.value && activeMenu.value === 'logs') {
                logTimer.value = setInterval(loadLogs, 2000); 
            }
        };

        const stopLogPoll = () => {
            if (logTimer.value) {
                clearInterval(logTimer.value);
                logTimer.value = null;
            }
        };

        const toggleLogPoll = () => {
            if (autoRefreshLogs.value) startLogPoll();
            else stopLogPoll();
        };

        const strmModule = window.useStrm(API_BASE, ElMessage, ElMessageBox);

        const getMenuTitle = (key) => ({ hot: '🔥 今日热门影视', movie: '🎬 本地电影总库', tv: '📺 本地剧集总库', discover: '🔍 全网跨平台搜索' }[key] || '');
        const formatFileSize = (bytes) => { if (bytes === 0) return '0 B'; const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB']; const i = Math.floor(Math.log(bytes) / Math.log(k)); return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]; };

        const loadConfig = async () => { try { const r = await axios.get(`${API_BASE}/config`); config.value = { ...config.value, ...r.data }; } catch (e) {} };
        const saveConfig = async () => { try { await axios.post(`${API_BASE}/config`, config.value); ElMessage.success('配置已保存'); } catch (e) { ElMessage.error('保存失败'); } };

        const loadLocalMedia = async (t, page = 1) => { 
            loading.value = true; 
            try { 
                const r = await axios.get(`${API_BASE}/local_media`, { params: { type: t, page: page, size: pageSize.value } }); 
                if (r.data && typeof r.data.items !== 'undefined') { 
                    lm.value = r.data.items; 
                    totalItems.value = r.data.total; 
                } else if (Array.isArray(r.data)) { 
                    lm.value = r.data; 
                    totalItems.value = r.data.length; 
                } else { 
                    lm.value = []; 
                    totalItems.value = 0; 
                } 
                currentPage.value = page; 
                const mainEl = document.querySelector('.el-main'); 
                if (mainEl) mainEl.scrollTo({ top: 0, behavior: 'smooth' }); 
            } catch (e) { 
                lm.value = []; 
                totalItems.value = 0; 
            } finally { 
                loading.value = false; 
            } 
        };
        
        const handlePageChange = (val) => loadLocalMedia(activeMenu.value, val);
        const loadSubscriptions = async () => { try { const r = await axios.get(`${API_BASE}/subscriptions`, { params: { status: 'pending' } }); subscriptions.value = r.data; } catch (e) {} };
        const loadRecords = async () => { try { const r = await axios.get(`${API_BASE}/subscriptions`, { params: { status: 'success' } }); records.value = r.data; } catch (e) {} };
        const loadLogs = async () => { try { const r = await axios.get(`${API_BASE}/logs`); systemLogs.value = r.data; } catch (e) {} };

        const fetchDriveFiles = async (parentId) => { driveLoading.value = true; try { const r = await axios.post(`${API_BASE}/drive/list`, { drive_type: currentDriveType.value, parent_id: parentId }); if(r.data.code === 200) driveFiles.value = r.data.data; else ElMessage.error(r.data.msg); } finally { driveLoading.value = false; } };
        const initDriveView = (type) => { currentDriveType.value = type; const rootId = type === 'quark' ? '0' : 'root'; drivePaths.value = [{ id: rootId, name: '全部文件' }]; fetchDriveFiles(rootId); };
        const clickDriveBreadcrumb = (index) => { drivePaths.value = drivePaths.value.slice(0, index + 1); fetchDriveFiles(drivePaths.value[index].id); };
        const openDriveFolder = (row) => { if (!row.is_folder) return; drivePaths.value.push({ id: row.id, name: row.name }); fetchDriveFiles(row.id); };
        const promptMkdir = async () => { try { const { value } = await msgBox.prompt('请输入文件夹名称', '新建'); if (value) { const pid = drivePaths.value[drivePaths.value.length - 1].id; const r = await axios.post(`${API_BASE}/drive/action`, { drive_type: currentDriveType.value, action: 'mkdir', file_id: pid, new_name: value }); if (r.data.code === 200) fetchDriveFiles(pid); } } catch(e){} };
        const promptRename = async (row) => { try { const { value } = await msgBox.prompt('请输入新名称', '重命名', { inputValue: row.name }); if (value) { const r = await axios.post(`${API_BASE}/drive/action`, { drive_type: currentDriveType.value, action: 'rename', file_id: row.id, new_name: value }); if (r.data.code === 200) fetchDriveFiles(drivePaths.value[drivePaths.value.length - 1].id); } } catch(e){} };
        const deleteDriveFile = async (row) => { try { await msgBox.confirm(`确定永久删除？`, '警告', { type: 'danger' }); const r = await axios.post(`${API_BASE}/drive/action`, { drive_type: currentDriveType.value, action: 'delete', file_id: row.id }); if (r.data.code === 200) fetchDriveFiles(drivePaths.value[drivePaths.value.length - 1].id); } catch(e){} };

        const handleMenuSelect = (i) => { 
            activeMenu.value = i; 
            selectedMediaList.value = []; 
            selectedTableRows.value = [];
            
            if (i === 'logs') {
                loadLogs();
                startLogPoll();
            } else {
                stopLogPoll();
            }

            if(['hot', 'movie', 'tv'].includes(i)) { currentPage.value = 1; loadLocalMedia(i, 1); }
            else if(i === 'subscriptions') loadSubscriptions(); 
            else if(i === 'records') loadRecords(); 
            else if(i === 'drive_quark') initDriveView('quark');
            else if(i === 'drive_aliyun') initDriveView('aliyun');
            else if(i === 'strm_configs') strmModule.loadStrmConfigs();
            else if(i === 'strm_records') { strmModule.recordPage.value = 1; strmModule.loadStrmRecords(); }
            else if(i === 'strm_tasks') { strmModule.loadStrmConfigs(); strmModule.loadStrmTasks(); }
            else if(i === 'strm_settings') strmModule.loadStrmSettings();
        };

        const searchTMDB = async () => { if (!sq.value) return; loading.value = true; try { const r = await axios.get(`${API_BASE}/search`, { params: { query: sq.value } }); sr.value = r.data.results.filter(x => x.media_type !== 'person'); } finally { loading.value = false; } };
        const isMediaSelected = (i) => selectedMediaList.value.some(m => (m.tmdb_id || m.id) === (i.tmdb_id || i.id));
        const toggleMediaSelect = (i, val) => { if (val) selectedMediaList.value.push(i); else selectedMediaList.value = selectedMediaList.value.filter(m => (m.tmdb_id || m.id) !== (i.tmdb_id || i.id)); };

        const subscribe = async (i, isL, force = false, driveType = '115') => { try { const r = await axios.post(`${API_BASE}/subscribe`, { tmdb_id: isL ? i.tmdb_id : i.id, media_type: i.media_type || 'movie', title: i.title || i.name, overview: i.overview, poster_path: i.poster_path, force: force, drive_type: driveType }); if (r.data.code === 409) { const dn = driveType==='quark'?'夸克':(driveType==='aliyun'?'阿里云':'115'); await ElMessageBox.confirm(`已在系统中！强制加入 [${dn}]？`, '提醒', {type: 'warning'}); await subscribe(i, isL, true, driveType); return; } ElMessage.success(`加入队列！`); i.sub_status = 'pending'; if(activeMenu.value === 'records') loadRecords(); } catch (e) {} };
        const batchSubscribe = async (driveType = '115') => { if (!selectedMediaList.value.length) return; const items = selectedMediaList.value.map(i => ({ tmdb_id: i.tmdb_id || i.id, media_type: i.media_type || 'movie', title: i.title || i.name, overview: i.overview || '', poster_path: i.poster_path || '', force: false, drive_type: driveType })); try { await axios.post(`${API_BASE}/subscribe/batch`, { items }); ElMessage.success(`批量操作成功！`); selectedMediaList.value = []; if(activeMenu.value === 'discover') searchTMDB(); else loadLocalMedia(activeMenu.value, currentPage.value); } catch (e) {} };
        const handleSelectionChange = (val) => { selectedTableRows.value = val; };
        const unsubscribeMedia = async (r) => { try { await ElMessageBox.confirm(`放弃订阅吗？`, '确认'); await axios.delete(`${API_BASE}/subscriptions/${r.tmdb_id}`); loadSubscriptions(); } catch (e) {} };
        const deleteRecord = async (r) => { try { await ElMessageBox.confirm(`清除此记录？`, '确认', { type: 'danger' }); await axios.delete(`${API_BASE}/subscriptions/${r.tmdb_id}`); loadRecords(); } catch (e) {} };
        const batchDeleteRecords = async () => { if (!selectedTableRows.value.length) return; try { await ElMessageBox.confirm(`删除记录？`, '确认', { type: 'danger' }); await axios.post(`${API_BASE}/subscriptions/batch_delete`, { tmdb_ids: selectedTableRows.value.map(r => r.tmdb_id) }); ElMessage.success('清理成功！'); selectedTableRows.value = []; if (activeMenu.value === 'subscriptions') loadSubscriptions(); else if (activeMenu.value === 'records') loadRecords(); } catch (e) {} };

        const openPanSou = async (i) => { if (!i) return; curMedia.value = i; const t = i.title || i.name; curKw.value = t; pr.value = {}; pv.value = true; ElMessage.info(`正在拉取...`); try { const r = await axios.get(`${API_BASE}/pansou_search`, { params: { kw: t } }); let d = r.data; if (d && d.data && d.data.merged_by_type) d = d.data; pr.value = d.merged_by_type || d || {}; } catch(e){} };
        const manualSaveLink = async (row, rawType) => { if (!curMedia.value) return; let dt = '115'; const rt = rawType.toLowerCase(); if(rt.includes('quark')) dt = 'quark'; if(rt.includes('aliyun')) dt = 'aliyun'; savingLink.value = true; try { const r = await axios.post(`${API_BASE}/save_link`, { tmdb_id: curMedia.value.tmdb_id || curMedia.value.id, media_type: curMedia.value.media_type || 'movie', title: curKw.value, poster_path: curMedia.value.poster_path || '', url: row.url, pwd: row.password || row.pwd || '', drive_type: dt }); if (r.data.code === 200) { ElMessage.success(r.data.message); pv.value = false; if(activeMenu.value === 'records') loadRecords(); if(activeMenu.value === 'subscriptions') loadSubscriptions(); } else ElMessage.error(r.data.message); } catch (e){} finally { savingLink.value = false; } };
        
        const runTaskManual = async () => { 
            try { 
                await axios.post(`${API_BASE}/tasks/trigger`); 
                ElMessage.success('进程已拉起，正在跳转系统日志监控...'); 
                setTimeout(() => { 
                    activeMenu.value = 'logs'; 
                    loadLogs(); 
                    startLogPoll(); 
                }, 1500); 
            } catch (e) {} 
        };
        
        const generate115QrCode = async () => {};

        onMounted(async () => { 
            await loadConfig(); 
            strmModule.loadStrmConfigs(); 
            strmModule.loadStrmSettings();
            
            loadLocalMedia('hot', 1); 
        });

        return { 
            activeMenu, syncingData, loading, lm, sr, sq, subscriptions, records, systemLogs, config, pv, pr, qrLoading, qUrl, qSt, curKw, currentPage, pageSize, totalItems, 
            selectedMediaList, selectedTableRows, isMediaSelected, toggleMediaSelect, batchSubscribe, handleSelectionChange, batchDeleteRecords,
            driveFiles, driveLoading, drivePaths, currentDriveType, formatFileSize, clickDriveBreadcrumb, openDriveFolder, promptMkdir, promptRename, deleteDriveFile,
            getMenuTitle, handleMenuSelect, saveConfig, searchTMDB, subscribe, unsubscribeMedia, deleteRecord, openPanSou, manualSaveLink, generate115QrCode, loadLogs, runTaskManual, handlePageChange,
            autoRefreshLogs, toggleLogPoll, 
            ...strmModule
        };
    }
});

if (typeof ElementPlusIconsVue !== 'undefined') { for (const [key, component] of Object.entries(ElementPlusIconsVue)) { app.component(key, component); } }
app.use(ElementPlus).mount('#app');