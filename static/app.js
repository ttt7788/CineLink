const { createApp, ref, onMounted, watch, computed } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;
const msgBox = ElMessageBox;
const API_BASE = '/api';

const app = createApp({
    setup() {
        const activeMenu = ref('hot'), syncingData = ref(false), loading = ref(false);
        const lm = ref([]), sr = ref([]), sq = ref(''), discoverHot = ref([]), discoverSearched = ref(false);
        const subscriptions = ref([]), records = ref([]), systemLogs = ref([]);
        const transferRecordPage = ref(1), transferRecordPageSize = 10, recordGroupPages = ref({}), bindingRefreshing = ref(false);
        const currentPage = ref(1), pageSize = ref(12), totalItems = ref(0);
        
        const selectedMediaList = ref([]);
        const selectedTableRows = ref([]);
        
        const driveFiles = ref([]);
        const driveLoading = ref(false);
        const drivePaths = ref([]); 
        const currentDriveType = ref(''); 
        
        const config = ref({ api_domain: '', image_domain: '', api_key: '', pansou_domain: '', pancheck_domain: '', pancheck_enabled: '1', cookie_115: '', cookie_quark: '', token_aliyun: '', drive115_save_dir: '0', quark_save_dir: '0', aliyun_save_dir: 'root', drive123_client_id: '', drive123_client_secret: '', drive123_save_dir: '0', cron_expression: '0 10,22 * * *', auto_subscribe_new: '0', auto_subscribe_drive: '115', magnet_download_drive: '115', ed2k_download_drive: '115', pipeline_auto_organize: '0', pipeline_organize_max_items: '30' });

        const pv = ref(false), pr = ref({}), curKw = ref('');
        const panSearchError = ref('');
        const panSearchSource = ref('');
        const curMedia = ref(null), savingLink = ref(false);
        const qrLoading = ref(false), qUrl = ref(''), qSt = ref(''), qTok = ref(null), pTimer = ref(null);
        const aliyunQrLoading = ref(false), aliyunQrUrl = ref(''), aliyunQrStatus = ref(''), aliyunQrToken = ref(null), aliyunQrTimer = ref(null);

        const autoRefreshLogs = ref(true);
        const logModuleFilter = ref('all');
        const logLevelFilter = ref('all');
        const logModules = ref([]);
        const logTimer = ref(null);
        const recycleConfig = ref({ enabled: '0', drives: ['115', 'aliyun', 'quark'], interval_hours: 24, password_115: '', last_run: '', drive_status: [] });
        const recycleItems = ref({});
        const recycleLoading = ref({});
        const transferTaskForm = ref({ urls: '' });
        const transferDownloadTasks = ref([]);
        const transferTaskLoading = ref(false);
        const organizerConfig = ref({
            drive_type: 'quark',
            source_dir: '0',
            movie_dir: '0',
            tv_dir: '0',
            max_items: 30,
            max_depth: 2,
            recursive: true,
            dry_run: true,
            movie_folder_rule: '{first_letter}-{title}-{year}',
            movie_file_rule: '{title}.{year}.{resource_pix}.{resource_source}.{video_encode}{ext}',
            tv_folder_rule: '{first_letter}-{title}-{year}',
            season_folder_rule: 'Season {season_num:02d}',
            episode_file_rule: '{title}.{year}.{season_episode}.{resource_pix}.{resource_source}.{video_encode}{ext}',
            category_strategy: '',
            wash_strategy: '',
        });
        const organizerVariables = [
            { k: '{original_name}', d: '原文件名', sample: '钢铁侠.2008.2160p.UHD.BluRay.x265.10bit.HDR.TrueHD.7.1-TnT.mkv' },
            { k: '{ext}', d: '扩展名', sample: 'mkv' },
            { k: '{title}', d: 'TMDB中的标题', sample: '钢铁侠' },
            { k: '{en_title}', d: 'TMDB中的英文标题，取决于识别数据', sample: 'Iron Man' },
            { k: '{first_letter}', d: '标题的大写拼音首字母', sample: 'G' },
            { k: '{year}', d: 'TMDB中的年份', sample: '2008' },
            { k: '{tmdb_id}', d: 'TMDB ID', sample: '1726' },
            { k: '{resource_pix}', d: '分辨率', sample: '2160p' },
            { k: '{resource_version}', d: '资源版本', sample: 'IMAX、HQ、3D、CC、DC' },
            { k: '{resource_source}', d: '资源来源', sample: 'USA.UHD、NF、DSNP' },
            { k: '{resource_type}', d: '资源质量', sample: 'BluRay、WEB-DL、HDTV' },
            { k: '{resource_effect}', d: '特效', sample: 'DV.HDR、DV、HDR、SDR' },
            { k: '{video_encode}', d: '视频编码', sample: 'H265.10bit、REMUX' },
            { k: '{audio_encode}', d: '音频编码', sample: 'TrueHD.7.1' },
            { k: '{resource_team}', d: '发布组', sample: 'TnT' },
            { k: '{fps}', d: '帧率', sample: '60FPS' },
            { k: '{season_episode}', d: '季集 SxxExx', sample: 'S01E01' },
            { k: '{season_num}', d: '季号', sample: '1' },
            { k: '{episode_num}', d: '集号', sample: '1' },
            { k: '{disc_num}', d: '盘号', sample: '1' },
            { k: '{season_name}', d: '季名', sample: '东海篇' },
            { k: '{season_year}', d: '季年份，可为空', sample: '1999' },
            { k: '{episode_name}', d: '集名', sample: '我是路飞！将要成为海贼王的男人！' },
            { k: '{custom_regex_match}', d: '自定义匹配', sample: '自定义匹配' },
        ];
        const organizerSyntax = [
            { k: 'cid115 / cid_quark / cid_aliyun / cid123', d: '二级分类策略里的单盘目录字段，分别对应 115、夸克、阿里云盘、123 云盘' },
            { k: 'target_id / folder_id / dir_id', d: '通用自定义目录字段；没有单盘目录字段时会作为目标目录 ID 使用' },
            { k: '{变量名}', d: '取这个变量的值' },
            { k: '<...>', d: '用尖括号包围的字符串块，块里 {变量名} 不为空时才取块里的内容' },
            { k: '简单来说重命名规则就是多个块，然后拼在一起', d: '适合把可选字段用 <...> 包起来' },
            { k: '<{{name}}...>', d: '给块取名字，之后可以用 {name} 反复引用该块的值' },
            { k: '<?{{name}}...>', d: '有名字的块可以只取名不输出，便于在规则后段引用' },
            { k: '{} 里支持 python 的字符串函数及语法', d: '如下方 replace/lower/upper/条件表达式' },
            { k: "{resource_effect.replace(' ', '')}", d: '替换 resource_effect 中的空格' },
            { k: '{resource_effect.lower()}', d: '将 resource_effect 转换为小写' },
            { k: '{resource_effect.upper()}', d: '将 resource_effect 转换为大写' },
            { k: "{'2160p' if resource_pix=='4k' else resource_pix}", d: '如果 resource_pix 为 4k，返回 2160p，否则返回原值' },
            { k: '<{title}> 和 {title} 的区别', d: '<{title}> 会先判断 title 是否为空，后者直接取 title 的值' },
            { k: '如果想用 { }，可用 [[ ]] 代替', d: '最终会替换为 { }，避免语法冲突' },
            { k: '{first_letter}-{title}-{year}-[tmdb={tmdb_id}]', d: '文件夹命名规则示例' },
            { k: '{title}.{year}.<{resource_pix}>.<{fps}>.<{resource_version}>.<{resource_source}>.<{resource_type}>.<{resource_effect}>.<{video_encode}>.<{audio_encode}>-<{resource_team}>', d: '电影命名规则示例' },
        ];
        const organizerPlan = ref([]);
        const organizerLoading = ref(false);

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

        const driveConfigMeta = {
            '115': { label: '115网盘', authKey: 'cookie_115', authLabel: '115 Cookie', saveDirKey: 'drive115_save_dir', saveDirLabel: '默认保存目录 ID', defaultRoot: '0' },
            aliyun: { label: '阿里云盘', authKey: 'token_aliyun', authLabel: '移动端 Refresh Token', saveDirKey: 'aliyun_save_dir', saveDirLabel: '默认保存目录 ID', defaultRoot: 'root' },
            quark: { label: '夸克网盘', authKey: 'cookie_quark', authLabel: '夸克 Cookie', saveDirKey: 'quark_save_dir', saveDirLabel: '默认保存目录 ID', defaultRoot: '0' },
            '123': { label: '123云盘', authKey: 'drive123_client_id', authLabel: 'Client ID / Secret', saveDirKey: 'drive123_save_dir', saveDirLabel: '默认保存目录 ID', defaultRoot: '0' },
        };
        const isConfiguredValue = (value) => String(value || '').trim().length > 0;
        const getDriveConfigStatus = (type) => {
            const key = String(type || '').replace('_internal', '');
            const meta = driveConfigMeta[key] || driveConfigMeta['115'];
            const authReady = key === '123'
                ? isConfiguredValue(config.value.drive123_client_id) && isConfiguredValue(config.value.drive123_client_secret)
                : isConfiguredValue(config.value[meta.authKey]);
            const saveDir = String(config.value[meta.saveDirKey] || meta.defaultRoot || '').trim();
            const missing = [];
            if (!authReady) missing.push(meta.authLabel);
            if (!isConfiguredValue(saveDir)) missing.push(meta.saveDirLabel);
            return {
                type: key,
                ...meta,
                authReady,
                saveDirReady: isConfiguredValue(saveDir),
                saveDir,
                missing,
                ready: authReady && isConfiguredValue(saveDir),
            };
        };
        const driveConfigCards = computed(() => ['115', 'aliyun', 'quark', '123'].map(type => getDriveConfigStatus(type)));
        const currentDriveStatus = computed(() => getDriveConfigStatus(currentDriveType.value || '115'));
        const pluginDriveOptions = computed(() => driveConfigCards.value.map(item => ({ label: item.label, value: item.type, ready: item.authReady })));
        const requireDriveReady = (type, feature = '该功能') => {
            const status = getDriveConfigStatus(type);
            if (status.ready) return true;
            const missing = [];
            if (!status.authReady) missing.push(status.authLabel);
            if (!status.saveDirReady) missing.push(status.saveDirLabel);
            ElMessage.warning(`${status.label} 未配置完整：${missing.join('、')}，${feature}已停止执行`);
            return false;
        };

        const strmModule = window.useStrm(API_BASE, ElMessage, ElMessageBox, { requireDriveReady, getDriveConfigStatus });

        const getMenuTitle = (key) => ({ hot: '今日热门影视', movie: '本地电影库', tv: '本地剧集库', discover: '全网聚合搜索' }[key] || '');
        const mediaPageMeta = computed(() => ({
            hot: { kicker: '发现资源', title: '今日热门', desc: '展示当天热门影视内容，适合快速搜盘、转存和入库。', tone: 'hot' },
            movie: { kicker: '媒体库', title: '全部电影库', desc: '浏览已同步的电影条目，按需搜索网盘资源并转存到指定目录。', tone: 'movie' },
            tv: { kicker: '媒体库', title: '全部剧集库', desc: '集中管理剧集资源，转存后可绑定网盘目录用于追更扫描。', tone: 'tv' },
        }[activeMenu.value] || { kicker: '媒体库', title: getMenuTitle(activeMenu.value), desc: '', tone: 'default' }));
        const discoverQuickKeywords = ref(['4K 杜比视界', '国配 动作', '科幻 2025', '豆瓣高分', '喜剧 合家欢', '悬疑 犯罪', '动画 电影', '韩剧 最新']);
        const discoverHotStats = computed(() => {
            const items = discoverHot.value || [];
            const movies = items.filter(i => (i.media_type || 'movie') === 'movie').length;
            return { total: items.length, movies, tv: Math.max(items.length - movies, 0) };
        });
        const mediaLibraryStats = computed(() => {
            const items = lm.value || [];
            const tv = items.filter(i => (i.media_type || activeMenu.value) === 'tv').length;
            const transferred = items.filter(i => i.sub_status === 'success').length;
            return {
                total: totalItems.value || items.length,
                current: items.length,
                movie: Math.max(items.length - tv, 0),
                tv,
                transferred,
            };
        });
        const driveMeta = {
            '115': { label: '115网盘', tag: 'info', icon: '115' },
            aliyun: { label: '阿里云盘', tag: 'primary', icon: '阿' },
            quark: { label: '夸克网盘', tag: 'success', icon: '夸' },
            '123': { label: '123云盘', tag: 'warning', icon: '123' },
            baidu: { label: '百度网盘', tag: 'info', icon: '百' },
        };
        const driveOrder = ['115', 'aliyun', 'quark', '123'];
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
        const recordSectionMetas = [
            { key: 'movie', label: '电影记录', tag: 'primary', empty: '暂无电影转存记录' },
            { key: 'tv', label: '剧集记录', tag: 'success', empty: '暂无剧集转存记录' },
        ];
        const normalizeMediaType = (mediaType) => mediaType === 'tv' ? 'tv' : 'movie';
        const getRecordSectionPageKey = (driveType, mediaType) => `${driveType || 'unknown'}:${mediaType}`;
        const getRecordGroupPage = (type, mediaType = 'all') => Number(recordGroupPages.value[getRecordSectionPageKey(type, mediaType)] || 1);
        const setRecordGroupPage = (type, mediaType, page) => {
            recordGroupPages.value = { ...recordGroupPages.value, [getRecordSectionPageKey(type, mediaType)]: page };
        };
        const pagedRecordGroups = computed(() => recordGroups.value.map(group => {
            const allItems = group.items || [];
            const sections = recordSectionMetas.map(meta => {
                const sectionItems = allItems.filter(item => normalizeMediaType(item.media_type) === meta.key);
                const total = sectionItems.length;
                const maxPage = Math.max(Math.ceil(total / transferRecordPageSize), 1);
                const page = Math.min(Math.max(getRecordGroupPage(group.type, meta.key), 1), maxPage);
                const start = (page - 1) * transferRecordPageSize;
                return {
                    ...meta,
                    total,
                    page,
                    allItems: sectionItems,
                    items: sectionItems.slice(start, start + transferRecordPageSize),
                    rangeText: total ? `${start + 1}-${Math.min(start + transferRecordPageSize, total)} / ${total}` : '0 / 0',
                };
            });
            return {
                ...group,
                allItems,
                sections,
                total: allItems.length,
                movieTotal: sections.find(section => section.key === 'movie')?.total || 0,
                tvTotal: sections.find(section => section.key === 'tv')?.total || 0,
            };
        }).filter(group => group.total > 0));
        const recordPageRangeText = computed(() => {
            if (!records.value.length) return '暂无转存记录';
            const start = (transferRecordPage.value - 1) * transferRecordPageSize + 1;
            const end = Math.min(transferRecordPage.value * transferRecordPageSize, records.value.length);
            return `当前显示 ${start}-${end} / ${records.value.length} 条`;
        });
        const getSeriesBindingText = (row) => {
            if (row.media_type !== 'tv') return '电影无需追更';
            if (!row.cloud_path) return '未绑定，点击右上刷新';
            return `${row.cloud_path} · 已识别 ${Number(row.latest_episode_count || 0)} 集`;
        };
        const getRecordGroupSummary = (group) => {
            const items = group.allItems || group.items || [];
            const tvItems = items.filter(item => item.media_type === 'tv');
            const bound = tvItems.filter(item => item.cloud_path).length;
            return `${items.length} 条成功记录，电影 ${group.movieTotal || 0}，剧集 ${group.tvTotal || 0}，剧集已绑定 ${bound} / 未绑定 ${Math.max(tvItems.length - bound, 0)}`;
        };
        const getRecordSectionSummary = (section) => {
            if (section.key === 'tv') {
                const bound = section.allItems.filter(item => item.cloud_path).length;
                return `已绑定 ${bound} / 未绑定 ${Math.max(section.total - bound, 0)}`;
            }
            return '电影无需追更绑定';
        };
        const panSearchStats = computed(() => {
            const groups = Object.values(pr.value || {}).filter(Array.isArray);
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
        const saveConfig = async () => {
            try {
                const r = await axios.post(`${API_BASE}/config`, config.value);
                if (r.data?.data?.cron_expression) config.value.cron_expression = r.data.data.cron_expression;
                ElMessage.success('配置已保存');
            } catch (e) {
                ElMessage.error('保存失败');
            }
        };

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
        const loadRecords = async () => { try { const r = await axios.get(`${API_BASE}/subscriptions`, { params: { status: 'success' } }); records.value = r.data; recordGroupPages.value = {}; transferRecordPage.value = 1; } catch (e) {} };
        const loadTransferDownloadTasks = async () => {
            transferTaskLoading.value = true;
            try {
                const r = await axios.get(`${API_BASE}/transfer_download/tasks`, { params: { limit: 200 } });
                transferDownloadTasks.value = Array.isArray(r.data) ? r.data : [];
            } catch (e) {
                ElMessage.error('读取转存下载任务失败');
            } finally {
                transferTaskLoading.value = false;
            }
        };
        const detectTransferLinkType = (url) => {
            const link = String(url || '').trim().toLowerCase();
            if (link.startsWith('magnet:?')) return 'magnet';
            if (link.startsWith('ed2k://')) return 'ed2k';
            if (link.includes('pan.baidu.com')) return 'baidu';
            if (link.includes('pan.quark.cn')) return 'quark';
            if (link.includes('alipan.com') || link.includes('aliyundrive.com')) return 'aliyun';
            if (link.includes('123pan.com') || link.includes('123684.com')) return '123';
            if (link.includes('115.com/s/') || link.includes('115cdn.com/s/')) return '115';
            return 'unknown';
        };
        const getTransferTaskTypeLabel = (row) => {
            if (row.link_type === 'magnet') return '磁力';
            if (row.link_type === 'ed2k') return 'ED2K';
            return '分享链接';
        };
        const getTransferTaskStatusType = (status) => {
            if (status === 'success') return 'success';
            if (status === 'failed') return 'danger';
            if (status === 'running') return 'warning';
            return 'info';
        };
        const submitTransferDownloadTasks = async () => {
            const urls = String(transferTaskForm.value.urls || '').split(/\r?\n/).map(v => v.trim()).filter(Boolean);
            if (!urls.length) return ElMessage.warning('请先粘贴网盘分享链接、磁力或 ED2K');
            transferTaskLoading.value = true;
            let successCount = 0;
            for (const url of urls) {
                const linkType = detectTransferLinkType(url);
                let driveType = transferTaskForm.value.drive_type || '';
                if (!driveType && linkType === 'magnet') driveType = config.value.magnet_download_drive || '115';
                if (!driveType && linkType === 'ed2k') driveType = config.value.ed2k_download_drive || '115';
                try {
                    const r = await axios.post(`${API_BASE}/transfer_download/tasks`, {
                        url,
                        drive_type: driveType
                    });
                    if (r.data.code === 202) successCount += 1;
                    else ElMessage.warning(r.data.message || '任务添加失败');
                } catch (e) {
                    ElMessage.error(e.response?.data?.message || e.response?.data?.detail || '任务添加失败');
                }
            }
            transferTaskLoading.value = false;
            if (successCount) {
                ElMessage.success(`已添加 ${successCount} 个任务`);
                transferTaskForm.value.urls = '';
                loadTransferDownloadTasks();
            }
        };
        const retryTransferDownloadTask = async (row) => {
            try {
                const r = await axios.post(`${API_BASE}/transfer_download/tasks/${row.id}/retry`);
                ElMessage.success(r.data.message || '已重新提交');
                loadTransferDownloadTasks();
            } catch (e) {
                ElMessage.error(e.response?.data?.message || e.response?.data?.detail || '重试失败');
            }
        };
        const deleteTransferDownloadTask = async (row) => {
            try {
                await ElMessageBox.confirm('删除这条转存下载任务记录？', '确认', { type: 'warning' });
                await axios.delete(`${API_BASE}/transfer_download/tasks/${row.id}`);
                ElMessage.success('已删除');
                loadTransferDownloadTasks();
            } catch (e) {}
        };
        const logLevelOptions = [
            { label: '全部级别', value: 'all' },
            { label: '成功', value: 'SUCCESS' },
            { label: '错误', value: 'ERROR' },
            { label: '警告', value: 'WARNING' },
            { label: '信息', value: 'INFO' },
        ];
        const logStats = computed(() => {
            const rows = systemLogs.value || [];
            return {
                total: rows.length,
                error: rows.filter(row => row.level === 'ERROR').length,
                warning: rows.filter(row => row.level === 'WARNING' || row.level === 'WARN').length,
                success: rows.filter(row => row.level === 'SUCCESS').length,
            };
        });
        const getLogLevelTagType = (level) => {
            if (level === 'SUCCESS') return 'success';
            if (level === 'ERROR') return 'danger';
            if (level === 'WARNING' || level === 'WARN') return 'warning';
            return 'info';
        };
        const loadLogModules = async () => {
            try {
                const r = await axios.get(`${API_BASE}/logs/modules`);
                logModules.value = Array.isArray(r.data) ? r.data : [];
            } catch (e) {
                logModules.value = [];
            }
        };
        const loadLogs = async () => {
            try {
                const r = await axios.get(`${API_BASE}/logs`, {
                    params: {
                        limit: 200,
                        module: logModuleFilter.value,
                        level: logLevelFilter.value,
                    }
                });
                systemLogs.value = Array.isArray(r.data) ? r.data : [];
                loadLogModules();
            } catch (e) {}
        };
        const resetLogFilters = () => {
            logModuleFilter.value = 'all';
            logLevelFilter.value = 'all';
            loadLogs();
        };
        const loadRecycleConfig = async () => {
            try {
                const r = await axios.get(`${API_BASE}/plugins/recycle/config`);
                recycleConfig.value = { ...recycleConfig.value, ...r.data, drives: r.data.drives || [] };
            } catch (e) {
                ElMessage.error('读取回收站插件配置失败');
            }
        };
        const saveRecycleConfig = async () => {
            try {
                await axios.post(`${API_BASE}/plugins/recycle/config`, recycleConfig.value);
                ElMessage.success('回收站插件配置已保存');
                loadRecycleConfig();
            } catch (e) {
                ElMessage.error(e.response?.data?.detail || '保存回收站插件配置失败');
            }
        };
        const getRecycleItems = (type) => recycleItems.value[type] || [];
        const loadRecycleItems = async (type) => {
            recycleLoading.value = { ...recycleLoading.value, [type]: true };
            try {
                const r = await axios.post(`${API_BASE}/plugins/recycle/list`, { drive_type: type });
                if (r.data.code === 200) {
                    recycleItems.value = { ...recycleItems.value, [type]: r.data.data || [] };
                } else {
                    recycleItems.value = { ...recycleItems.value, [type]: [] };
                    ElMessage.warning(r.data.msg || '读取回收站失败');
                }
            } catch (e) {
                recycleItems.value = { ...recycleItems.value, [type]: [] };
                ElMessage.error(e.response?.data?.detail || e.response?.data?.msg || '读取回收站失败');
            } finally {
                recycleLoading.value = { ...recycleLoading.value, [type]: false };
            }
        };
        const emptyRecyclebin = async (type) => {
            try {
                await ElMessageBox.confirm(`确定清空 ${getDriveLabel(type)} 回收站？该操作不可恢复。`, '危险操作', { type: 'danger' });
                const r = await axios.post(`${API_BASE}/plugins/recycle/empty`, { drive_type: type });
                if (r.data.code === 200) {
                    ElMessage.success(r.data.msg || '清空任务已提交');
                    loadRecycleItems(type);
                } else {
                    ElMessage.error(r.data.msg || '清空失败');
                }
            } catch (e) {
                if (e !== 'cancel' && e !== 'close') ElMessage.error(e.response?.data?.detail || e.response?.data?.msg || '清空失败');
            }
        };

        const fetchDriveFiles = async (parentId) => { driveLoading.value = true; try { const r = await axios.post(`${API_BASE}/drive/list`, { drive_type: currentDriveType.value, parent_id: parentId }); if(r.data.code === 200) driveFiles.value = r.data.data; else ElMessage.error(r.data.msg); } finally { driveLoading.value = false; } };
        const initDriveView = (type) => {
            currentDriveType.value = type;
            if (!requireDriveReady(type, '网盘文件管理')) {
                driveFiles.value = [];
                drivePaths.value = [];
                return;
            }
            const status = getDriveConfigStatus(type);
            const rootId = status.saveDir || status.defaultRoot || (type === 'aliyun' ? 'root' : '0');
            drivePaths.value = [{ id: rootId, name: '挂载目录' }];
            fetchDriveFiles(rootId);
        };
        const clickDriveBreadcrumb = (index) => { drivePaths.value = drivePaths.value.slice(0, index + 1); fetchDriveFiles(drivePaths.value[index].id); };
        const openDriveFolder = (row) => { if (!row.is_folder) return; drivePaths.value.push({ id: row.id, name: row.name }); fetchDriveFiles(row.id); };
        const promptMkdir = async () => { try { const { value } = await msgBox.prompt('请输入文件夹名称', '新建'); if (value) { const pid = drivePaths.value[drivePaths.value.length - 1].id; const r = await axios.post(`${API_BASE}/drive/action`, { drive_type: currentDriveType.value, action: 'mkdir', file_id: pid, new_name: value }); if (r.data.code === 200) fetchDriveFiles(pid); } } catch(e){} };
        const promptRename = async (row) => { try { const { value } = await msgBox.prompt('请输入新名称', '重命名', { inputValue: row.name }); if (value) { const r = await axios.post(`${API_BASE}/drive/action`, { drive_type: currentDriveType.value, action: 'rename', file_id: row.id, new_name: value }); if (r.data.code === 200) fetchDriveFiles(drivePaths.value[drivePaths.value.length - 1].id); } } catch(e){} };
        const deleteDriveFile = async (row) => { try { await msgBox.confirm(`确定永久删除？`, '警告', { type: 'danger' }); const r = await axios.post(`${API_BASE}/drive/action`, { drive_type: currentDriveType.value, action: 'delete', file_id: row.id }); if (r.data.code === 200) fetchDriveFiles(drivePaths.value[drivePaths.value.length - 1].id); } catch(e){} };

        const loadOrganizerConfig = async () => {
            try {
                const r = await axios.get(`${API_BASE}/drive_organizer/config`);
                organizerConfig.value = { ...organizerConfig.value, ...(r.data.data || {}) };
            } catch (e) {
                ElMessage.error('读取网盘整理配置失败');
            }
        };
        const saveOrganizerConfig = async () => {
            try {
                const r = await axios.post(`${API_BASE}/drive_organizer/config`, organizerConfig.value);
                organizerConfig.value = { ...organizerConfig.value, ...(r.data.data || {}) };
                ElMessage.success(r.data.message || '网盘整理配置已保存');
            } catch (e) {
                ElMessage.error(e.response?.data?.message || '保存网盘整理配置失败');
            }
        };
        const previewOrganizer = async () => {
            organizerLoading.value = true;
            try {
                const r = await axios.post(`${API_BASE}/drive_organizer/preview`, organizerConfig.value);
                if (r.data.code !== 200) throw new Error(r.data.message || '预览失败');
                organizerPlan.value = r.data.data.items || [];
                ElMessage.success(`已生成 ${organizerPlan.value.length} 条整理预览`);
            } catch (e) {
                organizerPlan.value = [];
                ElMessage.error(e.response?.data?.message || e.message || '预览失败');
            } finally {
                organizerLoading.value = false;
            }
        };
        const runOrganizer = async () => {
            try {
                await ElMessageBox.confirm(
                    organizerConfig.value.dry_run ? '当前是预览模式，不会移动文件。继续执行检查？' : '将按预览规则重命名并移动网盘文件，确认开始？',
                    '自动整理确认',
                    { type: organizerConfig.value.dry_run ? 'info' : 'warning' }
                );
            } catch (e) {
                return;
            }
            organizerLoading.value = true;
            try {
                const r = await axios.post(`${API_BASE}/drive_organizer/run`, organizerConfig.value);
                if (r.data.code !== 200) throw new Error(r.data.message || '整理失败');
                organizerPlan.value = r.data.data.items || [];
                ElMessage.success(`整理完成：成功 ${r.data.data.success}，失败 ${r.data.data.failed}`);
            } catch (e) {
                ElMessage.error(e.response?.data?.message || e.message || '整理失败');
            } finally {
                organizerLoading.value = false;
            }
        };

        const handleMenuSelect = (i) => { 
            activeMenu.value = i; 
            selectedMediaList.value = []; 
            selectedTableRows.value = [];
            
            if (i === 'logs') {
                loadLogModules();
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
            else if(i === 'transfer_task_add' || i === 'transfer_download_records') loadTransferDownloadTasks();
            else if(i === 'subscriptions') loadSubscriptions();
            else if(i === 'records') loadRecords();
            else if(i === 'drive_quark') initDriveView('quark');
            else if(i === 'drive_aliyun') initDriveView('aliyun');
            else if(i === 'drive_115') initDriveView('115');
            else if(i === 'drive_123') initDriveView('123');
            else if(i === 'drive_organizer') loadOrganizerConfig();
            else if(i === 'settings_center') loadRecycleConfig();
            else if(i === 'discover') { if (!discoverHot.value.length) loadDiscoverHot(); }
            else if(i === 'strm_configs') strmModule.loadStrmConfigs();
            else if(i === 'strm_records') { strmModule.recordPage.value = 1; strmModule.loadStrmRecords(); }
            else if(i === 'strm_tools') { strmModule.loadStrmConfigs(); strmModule.loadStrmTasks(); strmModule.loadStrmSettings(); }
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

        const subscribe = async (i, isL, force = false, driveType = '115') => { if (!requireDriveReady(driveType, '订阅转存')) return; try { const r = await axios.post(`${API_BASE}/subscribe`, { tmdb_id: isL ? i.tmdb_id : i.id, media_type: i.media_type || 'movie', title: i.title || i.name, overview: i.overview, poster_path: i.poster_path, force: force, drive_type: driveType }); if (r.data.code === 409) { const dn = getDriveLabel(driveType); await ElMessageBox.confirm(`已在系统中，是否强制加入 [${dn}]？`, '提醒', {type: 'warning'}); await subscribe(i, isL, true, driveType); return; } ElMessage.success(`已加入队列`); i.sub_status = 'pending'; if(activeMenu.value === 'records') loadRecords(); } catch (e) {} };
        const batchSubscribe = async (driveType = '115') => { if (!selectedMediaList.value.length || !requireDriveReady(driveType, '批量订阅转存')) return; const items = selectedMediaList.value.map(i => ({ tmdb_id: i.tmdb_id || i.id, media_type: i.media_type || 'movie', title: i.title || i.name, overview: i.overview || '', poster_path: i.poster_path || '', force: false, drive_type: driveType })); try { await axios.post(`${API_BASE}/subscribe/batch`, { items }); ElMessage.success(`批量操作成功`); selectedMediaList.value = []; if(activeMenu.value === 'discover') searchTMDB(); else loadLocalMedia(activeMenu.value, currentPage.value); } catch (e) {} };
        const handleSelectionChange = (val) => { selectedTableRows.value = val; };
        const unsubscribeMedia = async (r) => { try { await ElMessageBox.confirm(`放弃订阅吗？`, '确认'); await axios.delete(`${API_BASE}/subscriptions/${r.tmdb_id}`); loadSubscriptions(); } catch (e) {} };
        const deleteRecord = async (r) => { try { await ElMessageBox.confirm(`清除此记录？`, '确认', { type: 'danger' }); await axios.delete(`${API_BASE}/subscriptions/${r.tmdb_id}`); loadRecords(); } catch (e) {} };
        const batchDeleteRecords = async () => { if (!selectedTableRows.value.length) return; try { await ElMessageBox.confirm(`删除选中记录？`, '确认', { type: 'danger' }); await axios.post(`${API_BASE}/subscriptions/batch_delete`, { tmdb_ids: selectedTableRows.value.map(r => r.tmdb_id) }); ElMessage.success('清理成功'); selectedTableRows.value = []; if (activeMenu.value === 'subscriptions') loadSubscriptions(); else if (activeMenu.value === 'records') loadRecords(); } catch (e) {} };

        const openPanSou = async (i) => {
            if (!i) return;
            curMedia.value = i;
            const t = i.title || i.name;
            curKw.value = t;
            pr.value = {};
            panSearchError.value = '';
            panSearchSource.value = '';
            pv.value = true;
            ElMessage.info('正在拉取盘搜结果...');
            try {
                const r = await axios.get(`${API_BASE}/pansou_search`, { params: { kw: t } });
                const body = r.data || {};
                const data = body.data && body.data.merged_by_type ? body.data : body;
                const merged = data.merged_by_type || {};
                pr.value = Object.fromEntries(Object.entries(merged).filter(([, rows]) => Array.isArray(rows) && rows.length));
                panSearchSource.value = body.source || data.source || '';
                if (!Object.keys(pr.value).length) {
                    panSearchError.value = body.message || data.message || '没有搜索到可用网盘资源';
                    ElMessage.warning(panSearchError.value);
                }
            } catch (e) {
                panSearchError.value = e.response?.data?.message || e.response?.data?.detail || e.message || '盘搜请求失败';
                pr.value = {};
                ElMessage.error(panSearchError.value);
            }
        };
        const inferDriveType = (rawType, url) => {
            const rt = String(rawType || '').toLowerCase();
            const link = String(url || '').toLowerCase();
            if (rt.includes('quark') || link.includes('pan.quark.cn')) return 'quark';
            if (rt.includes('aliyun') || rt.includes('alipan') || link.includes('alipan.com') || link.includes('aliyundrive.com')) return 'aliyun';
            if (rt.includes('123') || link.includes('123pan.com')) return '123';
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

        const refreshSeriesBindings = async () => {
            bindingRefreshing.value = true;
            try {
                const r = await axios.post(`${API_BASE}/series_bindings/rebuild`);
                ElMessage.success(r.data.message || '已开始刷新剧集绑定');
                setTimeout(loadRecords, 1200);
            } catch (e) {
                ElMessage.error('刷新剧集绑定失败：' + (e.response?.data?.detail || e.message));
            } finally {
                bindingRefreshing.value = false;
            }
        };

        const manualBindSeriesDirectory = async (row) => {
            if (!row || row.media_type !== 'tv') return;
            const title = row.title || '未命名剧集';
            const driveLabel = getDriveLabel(row.drive_type);
            const placeholder = row.cloud_parent_id || '请输入网盘文件夹 ID，例如 0 / root / 文件夹ID';
            try {
                const { value } = await ElMessageBox.prompt(
                    `为《${title}》绑定 ${driveLabel} 的追剧目录。可填写：目录ID，或 目录ID|显示路径`,
                    '手动绑定追剧目录',
                    {
                        inputValue: row.cloud_parent_id || '',
                        inputPlaceholder: placeholder,
                        confirmButtonText: '绑定并扫描',
                        cancelButtonText: '取消',
                    }
                );
                const raw = String(value || '').trim();
                if (!raw) {
                    ElMessage.warning('请填写追剧目录 ID');
                    return;
                }
                const [cloudParentId, ...pathParts] = raw.split('|').map(part => part.trim());
                if (!cloudParentId) {
                    ElMessage.warning('请填写追剧目录 ID');
                    return;
                }
                const r = await axios.post(`${API_BASE}/series_bindings/manual`, {
                    tmdb_id: row.tmdb_id,
                    title,
                    drive_type: row.drive_type || '115',
                    cloud_parent_id: cloudParentId,
                    cloud_path: pathParts.join('|'),
                });
                ElMessage.success(r.data.message || '绑定成功');
                loadRecords();
                if (activeMenu.value === 'subscriptions') loadSubscriptions();
            } catch (e) {
                if (e === 'cancel' || e === 'close') return;
                ElMessage.error(e.response?.data?.detail || e.message || '绑定失败');
            }
        };
        
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
                    qSt.value = '登录成功，正在提取 Cookie...';
                    clearInterval(pTimer.value);
                    pTimer.value = null;
                    await axios.post(`${API_BASE}/115/login`, { uid: qTok.value.uid });
                    ElMessage.success('115 网盘授权成功');
                    loadConfig(); 
                    qUrl.value = ''; 
                } else if (st === -1 || st === -2) {
                    qSt.value = '二维码已过期或失效，请重新生成';
                    clearInterval(pTimer.value);
                    pTimer.value = null;
                }
            } catch (e) {
                // 网络抖动时静默处理
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
                ElMessage.error('无法获取 115 二维码：' + (e.response?.data?.detail || e.message));
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
            activeMenu, syncingData, loading, lm, sr, sq, discoverHot, discoverSearched, discoverQuickKeywords, discoverHotStats, mediaPageMeta, mediaLibraryStats, subscriptions, subscriptionGroups, subscriptionTotal, records, recordGroups, pagedRecordGroups, transferRecordPage, transferRecordPageSize, recordGroupPages, getRecordGroupPage, setRecordGroupPage, recordPageRangeText, bindingRefreshing, transferRecordTotal, recordStats, panSearchStats, panSearchError, panSearchSource, systemLogs, config, pv, pr, qrLoading, qUrl, qSt, aliyunQrLoading, aliyunQrUrl, aliyunQrStatus, curKw, currentPage, pageSize, totalItems,
            selectedMediaList, selectedTableRows, isMediaSelected, toggleMediaSelect, batchSubscribe, handleSelectionChange, batchDeleteRecords,
            driveFiles, driveLoading, drivePaths, currentDriveType, currentDriveStatus, driveConfigCards, pluginDriveOptions, getDriveConfigStatus, requireDriveReady, formatFileSize, clickDriveBreadcrumb, openDriveFolder, promptMkdir, promptRename, deleteDriveFile,
            recycleConfig, recycleItems, recycleLoading, loadRecycleConfig, saveRecycleConfig, loadRecycleItems, emptyRecyclebin, getRecycleItems,
            transferTaskForm, transferDownloadTasks, transferTaskLoading, loadTransferDownloadTasks, submitTransferDownloadTasks, retryTransferDownloadTask, deleteTransferDownloadTask, detectTransferLinkType, getTransferTaskTypeLabel, getTransferTaskStatusType,
            organizerConfig, organizerVariables, organizerSyntax, organizerPlan, organizerLoading, loadOrganizerConfig, saveOrganizerConfig, previewOrganizer, runOrganizer,
            getMenuTitle, getDriveLabel, getDriveTagType, getMediaTypeLabel, getDriveTypeName, getDriveTypeClass, getDriveTypeIcon, getSeriesBindingText, getRecordGroupSummary, getRecordSectionSummary, handleMenuSelect, saveConfig, searchTMDB, useDiscoverKeyword, subscribe, unsubscribeMedia, deleteRecord, openPanSou, manualSaveLink, checkLinkStatus, isTransferType, getCheckTagType, getCheckLabel, generate115QrCode, generateAliyunQrCode, refreshSeriesBindings, manualBindSeriesDirectory, loadLogs, loadLogModules, resetLogFilters, getLogLevelTagType, runTaskManual, handlePageChange,
            autoRefreshLogs, toggleLogPoll, logModuleFilter, logLevelFilter, logModules, logLevelOptions, logStats,
            ...strmModule
        };
    }
});

if (typeof ElementPlusIconsVue !== 'undefined') { for (const [key, component] of Object.entries(ElementPlusIconsVue)) { app.component(key, component); } }
app.use(ElementPlus).mount('#app');

