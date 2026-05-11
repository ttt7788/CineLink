const { createApp, ref, onMounted, watch, computed } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;
const msgBox = ElMessageBox;
const API_BASE = '/api';

const app = createApp({
    setup() {
        const activeMenu = ref('hot'), syncingData = ref(false), loading = ref(false);
        const lm = ref([]), sr = ref([]), sq = ref(''), discoverHot = ref([]), discoverSearched = ref(false);
        const subscriptions = ref([]), records = ref([]), systemLogs = ref([]);
        const currentPage = ref(1), pageSize = ref(30), totalItems = ref(0);
        
        const selectedMediaList = ref([]);
        const selectedTableRows = ref([]);
        
        const driveFiles = ref([]);
        const driveLoading = ref(false);
        const drivePaths = ref([]); 
        const currentDriveType = ref(''); 
        
        const config = ref({ api_domain: '', image_domain: '', api_key: '', pansou_domain: '', pancheck_domain: '', pancheck_enabled: '1', cookie_115: '', cookie_quark: '', token_aliyun: '', quark_save_dir: '0', aliyun_save_dir: 'root', cron_expression: '', cms_api_url: '', cms_api_token: '', auto_subscribe_new: '0', auto_subscribe_drive: '115' });

        const pv = ref(false), pr = ref({}), curKw = ref('');
        const curMedia = ref(null), savingLink = ref(false);
        const qrLoading = ref(false), qUrl = ref(''), qSt = ref(''), qTok = ref(null), pTimer = ref(null);
        const aliyunQrLoading = ref(false), aliyunQrUrl = ref(''), aliyunQrStatus = ref(''), aliyunQrToken = ref(null), aliyunQrTimer = ref(null);

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

        const getMenuTitle = (key) => ({ hot: '今日热门影视', movie: '本地电影库', tv: '本地剧集库', discover: '全网聚合搜索' }[key] || '');
        const discoverQuickKeywords = ref(['4K 杜比视界', '国配 动作', '科幻 2025', '豆瓣高分', '喜剧 合家欢', '悬疑 犯罪', '动画 电影', '韩剧 最新']);
        const discoverHotStats = computed(() => {
            const items = discoverHot.value || [];
            const movies = items.filter(i => (i.media_type || 'movie') === 'movie').length;
            return { total: items.length, movies, tv: Math.max(items.length - movies, 0) };
        });
        const driveMeta = {
            '115': { label: '115网盘', tag: 'info', icon: '115' },
            aliyun: { label: '阿里云盘', tag: 'primary', icon: '阿' },
            quark: { label: '夸克网盘', tag: 'success', icon: '夸' },
        };
        const driveOrder = ['115', 'aliyun', 'quark'];
        const normalizeDriveType = (driveType) => driveType || '115';
        const buildDriveGroups = (items) => {
            const source = items || [];
            const extraTypes = [...new Set(source.map(item => normalizeDriveType(item.drive_type)).filter(type => !driveOrder.includes(type)))];
            return [...driveOrder, ...extraTypes].map(type => ({
                type,
                ...(driveMeta[type] || { label: type || '未知网盘', tag: 'info', icon: String(type || '?').slice(0, 2) }),
                items: source.filter(item => normalizeDriveType(item.drive_type) === type),
            }));
        };
        const getDriveLabel = (driveType) => (driveMeta[driveType] || { label: driveType || '未知网盘' }).label;
        const getDriveTagType = (driveType) => (driveMeta[driveType] || { tag: 'info' }).tag;
        const getMediaTypeLabel = (mediaType) => mediaType === 'tv' ? '剧集' : '电影';
        const subscriptionGroups = computed(() => buildDriveGroups(subscriptions.value));
        const subscriptionTotal = computed(() => subscriptions.value.length);
        const recordGroups = computed(() => buildDriveGroups(records.value));
        const transferRecordTotal = computed(() => records.value.length);
        const recordStats = computed(() => {
            const items = records.value || [];
            const tv = items.filter(item => item.media_type === 'tv').length;
            return { total: items.length, movie: Math.max(items.length - tv, 0), tv };
        });
        const panSearchStats = computed(() => {
            const groups = Object.values(pr.value || {});
            const rows = groups.flatMap(items => items || []);
            return {
                total: rows.length,
                valid: rows.filter(row => row.check_status === 'valid').length,
                invalid: rows.filter(row => row.check_status === 'invalid').length,
                checking: rows.filter(row => row.check_status === 'checking' || row.check_status === 'pending').length,
            };
        });
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
        const loadDiscoverHot = async () => {
            try {
                const r = await axios.get(`${API_BASE}/local_media`, { params: { type: 'hot', page: 1, size: 8 } });
                discoverHot.value = r.data && Array.isArray(r.data.items) ? r.data.items : (Array.isArray(r.data) ? r.data.slice(0, 8) : []);
            } catch (e) {
                discoverHot.value = [];
            }
        };
        
        const handlePageChange = (val) => loadLocalMedia(activeMenu.value, val);
        const loadSubscriptions = async () => { try { const r = await axios.get(`${API_BASE}/subscriptions`, { params: { status: 'pending' } }); subscriptions.value = r.data; } catch (e) {} };
        const loadRecords = async () => { try { const r = await axios.get(`${API_BASE}/subscriptions`, { params: { status: 'success' } }); records.value = r.data; } catch (e) {} };
        const loadLogs = async () => { try { const r = await axios.get(`${API_BASE}/logs`); systemLogs.value = r.data; } catch (e) {} };

        const fetchDriveFiles = async (parentId) => { driveLoading.value = true; try { const r = await axios.post(`${API_BASE}/drive/list`, { drive_type: currentDriveType.value, parent_id: parentId }); if(r.data.code === 200) driveFiles.value = r.data.data; else ElMessage.error(r.data.msg); } finally { driveLoading.value = false; } };
        const initDriveView = (type) => { currentDriveType.value = type; const rootId = (type === 'quark' || type === '115') ? '0' : 'root'; drivePaths.value = [{ id: rootId, name: '全部文件' }]; fetchDriveFiles(rootId); };
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

            if (pTimer.value) {
                clearInterval(pTimer.value);
                pTimer.value = null;
            }
            if (aliyunQrTimer.value) {
                clearInterval(aliyunQrTimer.value);
                aliyunQrTimer.value = null;
            }

            if(['hot', 'movie', 'tv'].includes(i)) { currentPage.value = 1; loadLocalMedia(i, 1); }
            else if(i === 'subscriptions') loadSubscriptions();
            else if(i === 'records') loadRecords();
            else if(i === 'drive_quark') initDriveView('quark');
            else if(i === 'drive_aliyun') initDriveView('aliyun');
            else if(i === 'drive_115') initDriveView('115');
            else if(i === 'discover') { if (!discoverHot.value.length) loadDiscoverHot(); }
            else if(i === 'strm_configs') strmModule.loadStrmConfigs();
            else if(i === 'strm_records') { strmModule.recordPage.value = 1; strmModule.loadStrmRecords(); }
            else if(i === 'strm_tasks') { strmModule.loadStrmConfigs(); strmModule.loadStrmTasks(); }
            else if(i === 'strm_settings') strmModule.loadStrmSettings();
        };

        const searchTMDB = async () => {
            const query = (sq.value || '').trim();
            if (!query) return;
            discoverSearched.value = true;
            loading.value = true;
            try {
                const r = await axios.get(`${API_BASE}/search`, { params: { query } });
                sr.value = (r.data.results || []).filter(x => x.media_type !== 'person');
            } finally {
                loading.value = false;
            }
        };
        const useDiscoverKeyword = (keyword) => {
            sq.value = keyword;
            searchTMDB();
        };
        const isMediaSelected = (i) => selectedMediaList.value.some(m => (m.tmdb_id || m.id) === (i.tmdb_id || i.id));
        const toggleMediaSelect = (i, val) => { if (val) selectedMediaList.value.push(i); else selectedMediaList.value = selectedMediaList.value.filter(m => (m.tmdb_id || m.id) !== (i.tmdb_id || i.id)); };

        const subscribe = async (i, isL, force = false, driveType = '115') => { try { const r = await axios.post(`${API_BASE}/subscribe`, { tmdb_id: isL ? i.tmdb_id : i.id, media_type: i.media_type || 'movie', title: i.title || i.name, overview: i.overview, poster_path: i.poster_path, force: force, drive_type: driveType }); if (r.data.code === 409) { const dn = driveType==='quark'?'夸克':(driveType==='aliyun'?'阿里云':'115'); await ElMessageBox.confirm(`已在系统中！强制加入 [${dn}]？`, '提醒', {type: 'warning'}); await subscribe(i, isL, true, driveType); return; } ElMessage.success(`加入队列！`); i.sub_status = 'pending'; if(activeMenu.value === 'records') loadRecords(); } catch (e) {} };
        const batchSubscribe = async (driveType = '115') => { if (!selectedMediaList.value.length) return; const items = selectedMediaList.value.map(i => ({ tmdb_id: i.tmdb_id || i.id, media_type: i.media_type || 'movie', title: i.title || i.name, overview: i.overview || '', poster_path: i.poster_path || '', force: false, drive_type: driveType })); try { await axios.post(`${API_BASE}/subscribe/batch`, { items }); ElMessage.success(`批量操作成功！`); selectedMediaList.value = []; if(activeMenu.value === 'discover') searchTMDB(); else loadLocalMedia(activeMenu.value, currentPage.value); } catch (e) {} };
        const handleSelectionChange = (val) => { selectedTableRows.value = val; };
        const unsubscribeMedia = async (r) => { try { await ElMessageBox.confirm(`放弃订阅吗？`, '确认'); await axios.delete(`${API_BASE}/subscriptions/${r.tmdb_id}`); loadSubscriptions(); } catch (e) {} };
        const deleteRecord = async (r) => { try { await ElMessageBox.confirm(`清除此记录？`, '确认', { type: 'danger' }); await axios.delete(`${API_BASE}/subscriptions/${r.tmdb_id}`); loadRecords(); } catch (e) {} };
        const batchDeleteRecords = async () => { if (!selectedTableRows.value.length) return; try { await ElMessageBox.confirm(`删除记录？`, '确认', { type: 'danger' }); await axios.post(`${API_BASE}/subscriptions/batch_delete`, { tmdb_ids: selectedTableRows.value.map(r => r.tmdb_id) }); ElMessage.success('清理成功！'); selectedTableRows.value = []; if (activeMenu.value === 'subscriptions') loadSubscriptions(); else if (activeMenu.value === 'records') loadRecords(); } catch (e) {} };

        const openPanSou = async (i) => { if (!i) return; curMedia.value = i; const t = i.title || i.name; curKw.value = t; pr.value = {}; pv.value = true; ElMessage.info(`正在拉取...`); try { const r = await axios.get(`${API_BASE}/pansou_search`, { params: { kw: t } }); let d = r.data; if (d && d.data && d.data.merged_by_type) d = d.data; pr.value = d.merged_by_type || d || {}; } catch(e){} };
        const inferDriveType = (rawType, url) => {
            const rt = String(rawType || '').toLowerCase();
            const link = String(url || '').toLowerCase();
            if (rt.includes('quark') || link.includes('pan.quark.cn')) return 'quark';
            if (rt.includes('aliyun') || rt.includes('alipan') || link.includes('alipan.com') || link.includes('aliyundrive.com')) return 'aliyun';
            return '115';
        };
        const canCheckLink = (rawType, row) => {
            const rt = String(rawType || '').toLowerCase();
            const link = String(row?.url || '').toLowerCase();
            return rt.includes('quark') || rt.includes('aliyun') || rt.includes('alipan') || rt.includes('115') ||
                link.includes('pan.quark.cn') || link.includes('alipan.com') || link.includes('aliyundrive.com') ||
                link.includes('115.com/s/') || link.includes('115cdn.com/s/');
        };
        const isTransferType = (rawType, row) => {
            const type = String(rawType || '').toLowerCase();
            return ['quark', 'aliyun', 'alipan', '115', 'magnet', 'ed2k'].some(t => type.includes(t)) || canCheckLink(rawType, row);
        };
        const setRowCheckResult = (row, result) => {
            row.check_status = result?.status || 'unknown';
            row.check_valid = result?.valid;
            row.check_message = result?.message || '';
            row.check_source = result?.source || '';
        };
        const checkLinkStatus = async (row, rawType, quiet = false) => {
            if (!row?.url || !canCheckLink(rawType, row)) return { status: 'unsupported', valid: null, message: '暂不支持检测' };
            if (config.value.pancheck_enabled === '0') return { status: 'disabled', valid: null, message: '链接检测已关闭' };
            row.check_status = 'checking';
            row.check_message = '检测中...';
            const dt = inferDriveType(rawType, row.url);
            try {
                const r = await axios.post(`${API_BASE}/link/check`, {
                    url: row.url,
                    pwd: row.password || row.pwd || '',
                    drive_type: dt
                });
                const result = r.data.data || {};
                setRowCheckResult(row, result);
                if (!quiet && result.valid === false) ElMessage.error('链接失效：' + (result.message || '无法转存'));
                return result;
            } catch (e) {
                const result = { status: 'unknown', valid: null, message: e.response?.data?.detail || e.message };
                setRowCheckResult(row, result);
                return result;
            }
        };
        const checkSearchResults = async () => {
            if (config.value.pancheck_enabled === '0') return;
            const tasks = [];
            Object.entries(pr.value || {}).forEach(([type, links]) => {
                (links || []).forEach((row) => {
                    if (!canCheckLink(type, row)) return;
                    row.check_status = 'checking';
                    row.check_message = '检测中...';
                    tasks.push({ row, payload: { url: row.url, pwd: row.password || row.pwd || '', drive_type: inferDriveType(type, row.url) } });
                });
            });
            if (!tasks.length) return;
            try {
                const r = await axios.post(`${API_BASE}/link/check_batch`, { links: tasks.map(t => t.payload) });
                const results = r.data.data || [];
                tasks.forEach((task, index) => setRowCheckResult(task.row, results[index] || {}));
            } catch (e) {
                tasks.forEach(task => setRowCheckResult(task.row, { status: 'unknown', valid: null, message: e.response?.data?.detail || e.message }));
            }
        };
        const getCheckTagType = (status) => {
            if (status === 'valid') return 'success';
            if (status === 'invalid') return 'danger';
            if (status === 'checking' || status === 'pending') return 'warning';
            return 'info';
        };
        const getCheckLabel = (row) => {
            if (row.check_status === 'valid') return '有效';
            if (row.check_status === 'invalid') return '失效';
            if (row.check_status === 'checking') return '检测中';
            if (row.check_status === 'pending') return '待确认';
            if (row.check_status === 'disabled') return '已关闭';
            if (row.check_status === 'unsupported') return '不支持';
            return '待检测';
        };
        const getDriveTypeName = (type) => {
            const key = String(type || '').toLowerCase();
            if (key.includes('115')) return '115 网盘';
            if (key.includes('quark')) return '夸克网盘';
            if (key.includes('aliyun') || key.includes('alipan')) return '阿里云盘';
            if (key.includes('baidu')) return '百度网盘';
            if (key.includes('xunlei')) return '迅雷云盘';
            if (key.includes('magnet')) return '磁力资源';
            if (key.includes('ed2k')) return 'ED2K';
            if (key.includes('uc')) return 'UC 网盘';
            if (key.includes('tianyi')) return '天翼云盘';
            return String(type || '未知来源').toUpperCase();
        };
        const getDriveTypeClass = (type) => {
            const key = String(type || '').toLowerCase();
            if (key.includes('115')) return 'pan-type-115';
            if (key.includes('quark')) return 'pan-type-quark';
            if (key.includes('aliyun') || key.includes('alipan')) return 'pan-type-aliyun';
            return 'pan-type-other';
        };
        const getDriveTypeIcon = (type) => {
            const key = String(type || '').toLowerCase();
            if (key.includes('115')) return '115';
            if (key.includes('quark')) return '夸';
            if (key.includes('aliyun') || key.includes('alipan')) return '阿';
            if (key.includes('baidu')) return '百';
            if (key.includes('magnet')) return '磁';
            return '链';
        };

        const manualSaveLink = async (row, rawType) => {
            if (!curMedia.value) return;
            const dt = inferDriveType(rawType, row.url);
            savingLink.value = true;
            try {
                const checkResult = await checkLinkStatus(row, rawType, true);
                if (checkResult.valid === false) {
                    ElMessage.error('链接检测失败，已停止转存：' + (checkResult.message || '链接失效'));
                    return;
                }
                const r = await axios.post(`${API_BASE}/save_link`, {
                    tmdb_id: curMedia.value.tmdb_id || curMedia.value.id,
                    media_type: curMedia.value.media_type || 'movie',
                    title: curKw.value,
                    poster_path: curMedia.value.poster_path || '',
                    url: row.url,
                    pwd: row.password || row.pwd || '',
                    drive_type: dt
                });
                if (r.data.code === 200) {
                    ElMessage.success(r.data.message);
                    pv.value = false;
                    if(activeMenu.value === 'records') loadRecords();
                    if(activeMenu.value === 'subscriptions') loadSubscriptions();
                } else {
                    ElMessage.error(r.data.message);
                }
            } catch (e) {
                ElMessage.error('一键转存请求失败：' + (e.response?.data?.detail || e.message));
            } finally {
                savingLink.value = false;
            }
        };

        watch(pr, () => {
            if (pv.value) setTimeout(checkSearchResults, 0);
        });
        
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
        
        // 扫码状态轮询机制
        const poll115Status = async () => {
            if (!qTok.value) return;
            try {
                const r = await axios.post(`${API_BASE}/115/status`, { uid: qTok.value.uid, time: qTok.value.time, sign: qTok.value.sign });
                const d = r.data.data || {};
                const st = d.status;
                
                if (st === 0) {
                    qSt.value = '请使用 115 App 扫描上方二维码';
                } else if (st === 1) {
                    qSt.value = '已扫码，请在手机上点击确认登录';
                } else if (st === 2) {
                    qSt.value = '✅ 登录成功，正在提取 Cookie...';
                    clearInterval(pTimer.value);
                    pTimer.value = null;
                    await axios.post(`${API_BASE}/115/login`, { uid: qTok.value.uid });
                    ElMessage.success('115 网盘授权成功！');
                    loadConfig(); 
                    qUrl.value = ''; 
                } else if (st === -1 || st === -2) {
                    qSt.value = '❌ 二维码已过期或失效，请重新生成';
                    clearInterval(pTimer.value);
                    pTimer.value = null;
                }
            } catch (e) {
                // 网络抖动静默处理
            }
        };

        // 获取二维码与防错处理
        const generate115QrCode = async () => {
            if (pTimer.value) clearInterval(pTimer.value);
            qrLoading.value = true;
            qUrl.value = '';
            qSt.value = '正在向 115 请求二维码...';
            try {
                const r = await axios.get(`${API_BASE}/115/qrcode`);
                if (r.data && r.data.data) {
                    const d = r.data.data;
                    qTok.value = d;
                    qUrl.value = d.qrcode_image || '';
                    qSt.value = '请使用 115 App 扫码';
                    pTimer.value = setInterval(poll115Status, 2000);
                } else {
                    throw new Error("接口返回格式异常");
                }
            } catch (e) {
                qSt.value = '二维码请求失败';
                ElMessage.error('无法获取115二维码: ' + (e.response?.data?.detail || e.message));
            } finally {
                qrLoading.value = false;
            }
        };

        const stopAliyunQrPoll = () => {
            if (aliyunQrTimer.value) {
                clearInterval(aliyunQrTimer.value);
                aliyunQrTimer.value = null;
            }
        };

        const pollAliyunStatus = async () => {
            if (!aliyunQrToken.value) return;
            try {
                const r = await axios.post(`${API_BASE}/aliyun/status`, {
                    sid: aliyunQrToken.value.sid,
                    t: aliyunQrToken.value.t,
                    ck: aliyunQrToken.value.ck
                });
                const d = r.data.data || {};
                const st = d.status;
                if (st === 'WaitLogin') {
                    aliyunQrStatus.value = '请使用阿里云盘 App 扫描二维码';
                } else if (st === 'ScanSuccess') {
                    aliyunQrStatus.value = '已扫码，请在手机上确认授权';
                } else if (st === 'LoginSuccess') {
                    stopAliyunQrPoll();
                    aliyunQrStatus.value = '登录成功，移动端 Refresh Token 已写入配置';
                    ElMessage.success('阿里云盘移动端 Refresh Token 已自动写入');
                    await loadConfig();
                    aliyunQrUrl.value = '';
                } else if (st === 'TokenMissing') {
                    stopAliyunQrPoll();
                    aliyunQrStatus.value = d.message || '扫码已确认，但没有解析到移动端 Refresh Token';
                } else if (st === 'QRCodeExpired') {
                    stopAliyunQrPoll();
                    aliyunQrStatus.value = '二维码已过期，请重新生成';
                } else {
                    aliyunQrStatus.value = d.message || `当前状态：${st || '等待扫码'}`;
                }
            } catch (e) {
                aliyunQrStatus.value = '查询授权状态失败，稍后会继续重试';
            }
        };

        const generateAliyunQrCode = async () => {
            stopAliyunQrPoll();
            aliyunQrLoading.value = true;
            aliyunQrUrl.value = '';
            aliyunQrStatus.value = '正在生成阿里云盘移动端授权二维码...';
            try {
                const r = await axios.get(`${API_BASE}/aliyun/qrcode`);
                const d = r.data.data || {};
                if (!d.sid) throw new Error('接口未返回 sid');
                aliyunQrToken.value = d;
                aliyunQrUrl.value = d.qrcode_image || d.qrCodeUrl || '';
                aliyunQrStatus.value = '请使用阿里云盘 App 扫描二维码获取移动端 Refresh Token';
                aliyunQrTimer.value = setInterval(pollAliyunStatus, 2000);
            } catch (e) {
                aliyunQrStatus.value = '阿里云盘二维码请求失败';
                ElMessage.error('无法获取阿里云盘二维码：' + (e.response?.data?.detail || e.message));
            } finally {
                aliyunQrLoading.value = false;
            }
        };

        onMounted(async () => { 
            await loadConfig(); 
            strmModule.loadStrmConfigs(); 
            strmModule.loadStrmSettings();
            loadDiscoverHot();
            
            loadLocalMedia('hot', 1); 
        });

        return { 
            activeMenu, syncingData, loading, lm, sr, sq, discoverHot, discoverSearched, discoverQuickKeywords, discoverHotStats, subscriptions, subscriptionGroups, subscriptionTotal, records, recordGroups, transferRecordTotal, recordStats, panSearchStats, systemLogs, config, pv, pr, qrLoading, qUrl, qSt, aliyunQrLoading, aliyunQrUrl, aliyunQrStatus, curKw, currentPage, pageSize, totalItems,
            selectedMediaList, selectedTableRows, isMediaSelected, toggleMediaSelect, batchSubscribe, handleSelectionChange, batchDeleteRecords,
            driveFiles, driveLoading, drivePaths, currentDriveType, formatFileSize, clickDriveBreadcrumb, openDriveFolder, promptMkdir, promptRename, deleteDriveFile,
            getMenuTitle, getDriveLabel, getDriveTagType, getMediaTypeLabel, getDriveTypeName, getDriveTypeClass, getDriveTypeIcon, handleMenuSelect, saveConfig, searchTMDB, useDiscoverKeyword, subscribe, unsubscribeMedia, deleteRecord, openPanSou, manualSaveLink, checkLinkStatus, isTransferType, getCheckTagType, getCheckLabel, generate115QrCode, generateAliyunQrCode, loadLogs, runTaskManual, handlePageChange,
            autoRefreshLogs, toggleLogPoll, 
            ...strmModule
        };
    }
});

if (typeof ElementPlusIconsVue !== 'undefined') { for (const [key, component] of Object.entries(ElementPlusIconsVue)) { app.component(key, component); } }
app.use(ElementPlus).mount('#app');
