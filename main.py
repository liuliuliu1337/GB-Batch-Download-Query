import requests
import re
import time
import os
import json
import ddddocr
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin
import concurrent.futures
import threading
import pandas as pd
from datetime import datetime


class StandardDownloader:
    """单个标准下载器"""
    def __init__(self, hcno, standard_number, standard_name=""):
        self.hcno = hcno
        self.standard_number = standard_number
        self.standard_name = standard_name
        self.session = requests.Session()
        self.jsessionid = None
        self.no_fulltext = False
        self.fail_reason = None
        self.base_download_url = "http://c.gb688.cn/bzgk/gb/"
        try:
            self.ocr = ddddocr.DdddOcr(show_ad=False)
        except TypeError:
            self.ocr = ddddocr.DdddOcr()

        # 通用Headers（不再手工塞 Cookie，交给 Session 管理）
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        self.session.headers.update(self.headers)

    @property
    def download_page_url(self):
        return f"{self.base_download_url}showGb?type=download&hcno={self.hcno}"

    def open_download_page(self):
        """打开下载页并建立会话（站点会下发 JSESSIONID）

        2026 现状：该页首轮返回的是「标准详情页」，通过验证码后再次访问
        才会拿到下载凭据，所以这里的方法会被调用两次。
        """
        try:
            resp = self.session.get(self.download_page_url, timeout=30)
            self.jsessionid = self.session.cookies.get(
                "JSESSIONID", domain="c.gb688.cn", path="/bzgk"
            ) or self.session.cookies.get("JSESSIONID")

            # 已废止/未收录的标准，页面上没有「下载标准」按钮，只提示
            # “废止标准不提供标准文本阅读服务”，这种就不必再耗验证码了。
            # 注意：不能用“暂无全文”字样判断——那是内联 JS 的 i18n 字典，每页都有。
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                has_btn = soup.find("button", class_="xz_btn") is not None
                if not has_btn:
                    self.no_fulltext = True
                    self.fail_reason = "无全文（已废止或未收录）"
            except Exception:
                pass

            return resp.status_code == 200
        except Exception as e:
            print(f"{self.standard_number}: 打开下载页异常: {e}")
            return False

    def get_verify_code(self):
        """获取验证码并识别"""
        timestamp = int(time.time() * 1000)
        url = f"{self.base_download_url}gc?t={timestamp}"

        headers = {"Referer": self.download_page_url}
        response = self.session.get(url, headers=headers, timeout=30)

        if response.status_code == 200 and "image" in (response.headers.get("Content-Type") or ""):
            verify_code = self.ocr.classification(response.content)
            verify_code = ''.join(c for c in verify_code if c.isalnum()).upper()

            if len(verify_code) != 4:
                if len(verify_code) < 4:
                    verify_code = verify_code.ljust(4, 'X')
                else:
                    verify_code = verify_code[:4]

            return verify_code
        return None

    def submit_verify_code(self, code):
        """提交验证码，返回是否通过"""
        url = f"{self.base_download_url}verifyCode"
        headers = {
            "Accept": "text/plain, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "http://c.gb688.cn",
            "Referer": self.download_page_url,
        }
        try:
            response = self.session.post(url, data={"verifyCode": code}, headers=headers, timeout=15)
            return response.status_code == 200 and "success" in response.text.strip().lower()
        except Exception:
            return False

    def download_pdf(self):
        """下载PDF文件"""
        self.fail_reason = None

        # 第一步：打开下载页，建立会话
        if not self.open_download_page():
            self.fail_reason = "打开下载页失败"
            print(f"{self.standard_number}: 打开下载页失败")
            return None

        if self.no_fulltext:
            self.fail_reason = self.fail_reason or "无全文（已废止或未收录）"
            print(f"\r{self.standard_number}: 无全文，跳过")
            return None

        # 第二步：验证验证码
        max_attempts = 10
        passed = False
        for attempt in range(max_attempts):
            code = self.get_verify_code()
            if not code:
                print(f"{self.standard_number}: 获取验证码失败，尝试 {attempt+1}/{max_attempts}")
                time.sleep(1)
                continue

            if self.submit_verify_code(code):
                passed = True
                break

            print(f"{self.standard_number}: 验证码识别错误({code})，尝试 {attempt+1}/{max_attempts}")
            time.sleep(1)

        if not passed:
            self.fail_reason = "验证码连续失败"
            print(f"{self.standard_number}: 验证码连续失败，放弃")
            return None

        # 第三步：关键一步 —— 验证通过后必须再访问一次下载页（带 request_locale），
        # 这一跳才会真正下发下载凭据，不做这步 viewGb 只会返回 0 字节
        try:
            self.session.get(self.download_page_url + "&request_locale=zh_CN",
                             headers={"Referer": self.download_page_url}, timeout=30)
        except Exception as e:
            print(f"{self.standard_number}: 获取下载凭据异常: {e}")
        time.sleep(0.5)

        # 第四步：下载PDF
        pdf_url = f"{self.base_download_url}viewGb?hcno={self.hcno}"
        headers = {
            "Upgrade-Insecure-Requests": "1",
            "Referer": self.download_page_url,
        }

        try:
            response = self.session.get(pdf_url, headers=headers, stream=True, timeout=120)

            if response.status_code != 200:
                self.fail_reason = f"状态码 {response.status_code}"
                print(f"{self.standard_number}: 下载失败，状态码: {response.status_code}")
                return None

            safe_number = self.standard_number.replace("/", "_").replace(" ", "_").replace("\\", "_")
            filename = f"{safe_number}.pdf"

            counter = 1
            while os.path.exists(filename):
                filename = f"{safe_number}_{counter}.pdf"
                counter += 1

            content_length = response.headers.get('Content-Length', '0')
            total_size = int(content_length) if content_length.isdigit() else 0

            downloaded_size = 0
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            percent = (downloaded_size / total_size) * 100
                            print(f"\r{self.standard_number}: 下载进度 {percent:.1f}% ({downloaded_size}/{total_size})", end='')

            if downloaded_size > 1000:
                with open(filename, 'rb') as f:
                    header = f.read(5)
                if header == b'%PDF-':
                    print(f"\r{self.standard_number}: 下载成功 ({downloaded_size} 字节)")
                    return {
                        "filename": filename,
                        "standard_number": self.standard_number,
                        "standard_name": self.standard_name,
                        "size": downloaded_size
                    }
                os.remove(filename)
                self.fail_reason = "返回内容不是PDF"
                print(f"\r{self.standard_number}: 返回内容不是PDF（多半是未通过验证）")
                return None

            self.fail_reason = "未取到PDF（0 字节，通常是下载凭据没拿到）"
            print(f"\r{self.standard_number}: 文件无效或太小 ({downloaded_size} 字节)")
            if os.path.exists(filename):
                os.remove(filename)
            return None

        except Exception as e:
            self.fail_reason = f"异常: {e}"
            print(f"\r{self.standard_number}: 下载异常: {e}")
            return None


class GBBatchSearchDownloader:
    """GB标准批量搜索下载器"""
    def __init__(self):
        self.base_search_url = "https://openstd.samr.gov.cn/bzgk/gb/"
        self.session = requests.Session()
        self.search_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    def search_standards(self, keyword, max_results=10):
        """搜索标准"""
        encoded_keyword = quote(keyword)
        search_url = f"{self.base_search_url}std_list?p.p1=0&p.p90=circulation_date&p.p91=desc&p.p2={encoded_keyword}"

        print(f"搜索: {keyword}")

        try:
            response = self.session.get(search_url, headers=self.search_headers, timeout=30)
            if response.status_code == 200:
                return self.parse_search_results(response.text, max_results)
            print(f"搜索失败，状态码: {response.status_code}")
            return []
        except Exception as e:
            print(f"搜索异常: {e}")
            return []

    def parse_search_results(self, html_content, max_results):
        """解析搜索结果 —— 按表头名称定位列，避免网站改版后错位"""
        soup = BeautifulSoup(html_content, "html.parser")
        standards = []

        table = soup.find("table", class_="table")
        if not table:
            print("未找到搜索结果表格")
            return standards

        # 现行表头：序号 / 标准号 / 语种 / 是否采标 / 标准名称 / 类别 / 状态 / 发布日期 / 实施日期 / 操作
        header_names = [th.get_text(strip=True) for th in table.find_all("th")]
        col = {name: i for i, name in enumerate(header_names)}

        i_number = col.get("标准号", 1)
        i_adopt = col.get("是否采标", 3)
        i_name = col.get("标准名称", 4)
        i_cat = col.get("类别", 5)
        i_status = col.get("状态", 6)
        i_pub = col.get("发布日期", 7)
        i_imp = col.get("实施日期", 8)
        need = max(i_number, i_adopt, i_name, i_cat, i_status, i_pub, i_imp) + 1

        rows = table.find_all("tr")
        if len(rows) <= 1:
            print("未找到搜索结果")
            return standards

        body_rows = rows[1:max_results + 1] if max_results else rows[1:]
        for i, row in enumerate(body_rows):
            try:
                cells = row.find_all("td")
                if len(cells) < need:
                    continue

                info = {}

                # hcno：整行找 32 位十六进制标识
                raw_row = str(row)
                m = re.search(r"['\"]([0-9A-Fa-f]{20,})['\"]", raw_row)
                if m:
                    info["hcno"] = m.group(1).upper()
                else:
                    m = re.search(r"(?:showInfo|showGb)\(\s*['\"]([0-9A-Fa-f]+)['\"]", raw_row)
                    if m:
                        info["hcno"] = m.group(1).upper()

                info["standard_number"] = cells[i_number].get_text(strip=True)
                info["adopted_standard"] = cells[i_adopt].get_text(strip=True)
                info["standard_name"] = cells[i_name].get_text(strip=True)
                info["category"] = cells[i_cat].get_text(strip=True)
                info["status"] = cells[i_status].get_text(strip=True)
                info["publish_date"] = cells[i_pub].get_text(strip=True)
                info["implement_date"] = cells[i_imp].get_text(strip=True)

                if not info.get("hcno"):
                    print(f"  第{i+1}行缺少 hcno，跳过")
                    continue
                if not info["standard_number"]:
                    continue

                standards.append(info)
                print(f"  {i+1}. {info['standard_number']} - {info['standard_name'][:40]}")

            except Exception as e:
                print(f"解析第{i+1}行时出错: {e}")
                continue

        print(f"找到 {len(standards)} 个标准")
        return standards

    def batch_download(self, standards_list, max_workers=1):
        """批量下载：每个标准独立会话+独立验证码"""
        if not standards_list:
            print("没有可下载的标准")
            return [], []

        print(f"\n开始批量下载 {len(standards_list)} 个标准...")
        print(f"使用 {max_workers} 个并发线程")
        print("=" * 60)

        downloaded_files = []
        failed_downloads = []
        task_map = {}

        def job(info):
            d = StandardDownloader(
                hcno=info["hcno"],
                standard_number=info["standard_number"],
                standard_name=info.get("standard_name", "")
            )
            r = d.download_pdf()
            return r, d.fail_reason

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for standard_info in standards_list:
                future = executor.submit(job, standard_info)
                futures.append(future)
                task_map[future] = standard_info
                time.sleep(0.5)

            for future in concurrent.futures.as_completed(futures):
                std_info = task_map[future]
                try:
                    result, reason = future.result(timeout=300)
                    if result:
                        downloaded_files.append(result)
                    else:
                        failed_downloads.append({
                            "standard_number": std_info["standard_number"],
                            "standard_name": std_info.get("standard_name", ""),
                            "error": reason or "下载失败"
                        })
                except concurrent.futures.TimeoutError:
                    failed_downloads.append({
                        "standard_number": std_info["standard_number"],
                        "standard_name": std_info.get("standard_name", ""),
                        "error": "任务超时"
                    })
                except Exception as e:
                    failed_downloads.append({
                        "standard_number": std_info["standard_number"],
                        "standard_name": std_info.get("standard_name", ""),
                        "error": str(e)
                    })

        print(f"\n{'='*60}")
        print("批量下载完成!")
        print(f"{'='*60}")

        if downloaded_files:
            print(f"\n成功下载: {len(downloaded_files)} 个")
            total_size = 0
            for item in downloaded_files:
                print(f"  - {item['standard_number']:20} -> {item['filename']}")
                total_size += item.get('size', 0)
            if total_size > 0:
                print(f"  总计大小: {total_size:,} 字节 ({total_size/1024/1024:.2f} MB)")

        if failed_downloads:
            print(f"\n失败: {len(failed_downloads)} 个")
            for item in failed_downloads:
                print(f"  - {item['standard_number']:20} -> {item.get('error', '未知错误')}")

        return downloaded_files, failed_downloads

    def read_standards_from_txt(self, filepath):
        """从txt文件读取标准号"""
        if not os.path.exists(filepath):
            print(f"文件不存在: {filepath}")
            return []

        standards = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            print(f"读取文件: {filepath} ({len(lines)} 行)")

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                for sep in (',', ';', '\t'):
                    if sep in line:
                        parts = [p.strip() for p in line.split(sep)]
                        standards.extend([p for p in parts if p and not p.startswith('#')])
                        break
                else:
                    standards.append(line)

            unique_standards = []
            seen = set()
            for std in standards:
                if std not in seen:
                    seen.add(std)
                    unique_standards.append(std)

            print(f"从文件中提取到 {len(unique_standards)} 个标准号")
            if unique_standards:
                preview = ', '.join(unique_standards[:5])
                if len(unique_standards) > 5:
                    preview += f"... 等 {len(unique_standards)} 个"
                print(f"标准号列表: {preview}")
            return unique_standards

        except Exception as e:
            print(f"读取文件失败: {e}")
            return []

    def search_and_download_from_txt(self, filepath, max_workers=1):
        """从txt文件读取标准号并批量下载"""
        standard_numbers = self.read_standards_from_txt(filepath)
        if not standard_numbers:
            print("没有读取到有效的标准号")
            return

        print("\n将搜索并下载以下标准:")
        for i, std in enumerate(standard_numbers, 1):
            print(f"  {i}. {std}")

        confirm = input(f"\n确认搜索并下载以上 {len(standard_numbers)} 个标准？(y/n): ").strip().lower()
        if confirm != 'y':
            print("操作取消")
            return

        print("\n正在搜索标准信息...")
        all_standards = []
        not_found = []

        for i, std_number in enumerate(standard_numbers, 1):
            print(f"\n[{i}/{len(standard_numbers)}] 搜索: {std_number}")
            standards = self.search_standards(std_number, max_results=1)

            if standards:
                all_standards.append(standards[0])
                print(f"    找到: {standards[0]['standard_number']} - {standards[0]['standard_name'][:30]}")
            else:
                not_found.append(std_number)
                print(f"    未找到: {std_number}")

            time.sleep(1)

        if not_found:
            print("\n以下标准未找到:")
            for std in not_found:
                print(f"  - {std}")

        if not all_standards:
            print("没有找到任何标准")
            return

        print(f"\n找到 {len(all_standards)} 个标准，开始下载...")
        if max_workers <= 0:
            max_workers = input("并发下载数 (默认1，建议1-2): ").strip()
            max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1

        self.batch_download(all_standards, max_workers)

    def create_example_txt_file(self, filepath="standards_example.txt"):
        """创建示例txt文件"""
        example_content = """# GB标准号列表文件
# 支持格式：
# 1. 每行一个标准号（可包含空格）
# 2. 用逗号分隔的多个标准号
# 3. 用分号分隔的多个标准号
# 4. 空行和以#开头的注释行会被忽略

# 示例1：每行一个标准号（推荐格式）
GB/T 28448-2019
GB/T 22239-2019
GB/T 25058-2019

# 示例2：一行多个标准号（逗号分隔）
GB/T 28449-2018, GB/T 25070-2019, GB/T 20271-2006

# 示例3：一行多个标准号（分号分隔）
GB/T 20984-2007; GB/T 20273-2006; GB/T 18336-2008

# 注意：不要在标准号内部使用逗号或分号
"""

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(example_content)
            print(f"示例文件已创建: {filepath}")
            return True
        except Exception as e:
            print(f"创建示例文件失败: {e}")
            return False

    def batch_search_info_to_excel(self, txt_file=None, excel_file=None):
        """从txt文件批量搜索标准信息并导出为Excel"""
        if not txt_file:
            txt_file = input("请输入包含标准号的txt文件路径 (直接回车使用默认文件standards.txt): ").strip()
            if not txt_file:
                txt_file = "standards.txt"

        if not os.path.exists(txt_file):
            print(f"文件不存在: {txt_file}")
            if input("是否创建示例文件？(y/n): ").strip().lower() == 'y':
                self.create_example_txt_file(txt_file)
                print(f"请编辑 {txt_file} 文件，添加标准号后再运行此功能")
            else:
                print("操作取消")
            return

        standard_numbers = self.read_standards_from_txt(txt_file)
        if not standard_numbers:
            print("没有读取到有效的标准号")
            return

        if not excel_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_excel = f"standards_info_{timestamp}.xlsx"
            excel_file = input(f"请输入导出的Excel文件路径 (直接回车使用 {default_excel}): ").strip()
            if not excel_file:
                excel_file = default_excel

        print(f"\n将要搜索以下 {len(standard_numbers)} 个标准的信息:")
        for i, std in enumerate(standard_numbers[:10], 1):
            print(f"  {i}. {std}")
        if len(standard_numbers) > 10:
            print(f"  ... 等 {len(standard_numbers)} 个标准")

        if input("\n确认搜索并导出到Excel？(y/n): ").strip().lower() != 'y':
            print("操作取消")
            return

        print("\n开始批量搜索标准信息...")
        print("=" * 80)

        all_standards_info = []
        not_found_standards = []

        total = len(standard_numbers)
        for i, std_number in enumerate(standard_numbers, 1):
            print(f"\n[{i}/{total}] 搜索: {std_number}")
            try:
                standards = self.search_standards(std_number, max_results=1)
                if standards:
                    std_info = standards[0]
                    std_info["search_keyword"] = std_number
                    std_info["found"] = "是"
                    all_standards_info.append(std_info)
                    print(f"    找到: {std_info['standard_number']} - {std_info['standard_name'][:30]}")
                else:
                    not_found_standards.append({
                        "search_keyword": std_number, "standard_number": std_number,
                        "standard_name": "", "adopted_standard": "", "category": "",
                        "status": "", "publish_date": "", "implement_date": "",
                        "found": "否", "error": "未找到"
                    })
                    print(f"    未找到: {std_number}")
            except Exception as e:
                not_found_standards.append({
                    "search_keyword": std_number, "standard_number": std_number,
                    "standard_name": "", "adopted_standard": "", "category": "",
                    "status": "", "publish_date": "", "implement_date": "",
                    "found": "否", "error": str(e)[:50]
                })
                print(f"    搜索出错: {std_number} - {e}")
            time.sleep(1)

        all_results = all_standards_info + not_found_standards

        try:
            df = pd.DataFrame(all_results)
            column_order = [
                "search_keyword", "found", "standard_number", "standard_name",
                "adopted_standard", "category", "status",
                "publish_date", "implement_date", "hcno", "error"
            ]
            existing_columns = [c for c in column_order if c in df.columns]
            df = df[existing_columns]

            with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='标准信息')
                worksheet = writer.sheets['标准信息']
                for i, c in enumerate(df.columns, 1):
                    width = 40 if c == "standard_name" else (20 if c == "standard_number" else 15)
                    worksheet.column_dimensions[chr(64 + i)].width = width

            print(f"\n{'='*80}")
            print("搜索结果统计:")
            print(f"{'='*80}")
            print(f"找到: {len(all_standards_info)} 个标准")
            print(f"未找到: {len(not_found_standards)} 个标准")
            print(f"总计: {len(all_results)} 个标准")
            print(f"Excel文件已保存: {excel_file}")

            if os.path.exists(excel_file):
                file_size = os.path.getsize(excel_file)
                print(f"文件大小: {file_size:,} 字节 ({file_size/1024:.1f} KB)")

            if input("\n是否打开Excel文件？(y/n): ").strip().lower() == 'y':
                try:
                    os.startfile(excel_file)
                    print("正在打开Excel文件...")
                except Exception:
                    print("无法自动打开文件，请手动打开。")

            return excel_file

        except Exception as e:
            print(f"导出Excel失败: {e}")
            print("尝试导出为CSV格式...")
            try:
                csv_file = excel_file.replace('.xlsx', '.csv')
                pd.DataFrame(all_results).to_csv(csv_file, index=False, encoding='utf-8-sig')
                print(f"CSV文件已保存: {csv_file}")
                return csv_file
            except Exception as e2:
                print(f"导出CSV也失败: {e2}")
                return None

    def search_detailed_info(self, standard_number):
        """搜索单个标准的详细信息"""
        print(f"搜索标准详情: {standard_number}")
        try:
            standards = self.search_standards(standard_number, max_results=1)
            if standards:
                s = standards[0]
                print("\n" + "=" * 60)
                print(f"标准号: {s.get('standard_number', 'N/A')}")
                print(f"标准名称: {s.get('standard_name', 'N/A')}")
                print(f"是否采标: {s.get('adopted_standard', 'N/A')}")
                print(f"类别: {s.get('category', 'N/A')}")
                print(f"状态: {s.get('status', 'N/A')}")
                print(f"发布日期: {s.get('publish_date', 'N/A')}")
                print(f"实施日期: {s.get('implement_date', 'N/A')}")
                print(f"HCNO: {s.get('hcno', 'N/A')}")
                print("=" * 60)
                return s
            print(f"未找到标准: {standard_number}")
            return None
        except Exception as e:
            print(f"搜索失败: {e}")
            return None

    def run_interactive_mode(self):
        """交互式运行模式"""
        print("GB标准批量搜索下载工具")
        print("=" * 60)

        while True:
            print("\n操作选项:")
            print("1. 搜索标准并下载")
            print("2. 直接输入标准号下载")
            print("3. 从txt文件读取标准号并下载")
            print("4. 批量搜索标准信息并导出Excel")
            print("5. 搜索单个标准详细信息")
            print("6. 创建示例txt文件")
            print("7. 退出")

            choice = input("请选择 (1-7): ").strip()

            if choice == "1":
                self.search_and_download()
            elif choice == "2":
                self.direct_download()
            elif choice == "3":
                self.txt_file_download()
            elif choice == "4":
                self.batch_search_info_to_excel()
            elif choice == "5":
                self.search_single_standard()
            elif choice == "6":
                self.create_example_txt_file()
                continue
            elif choice == "7":
                print("退出程序")
                break
            else:
                print("无效选择")
                continue

            if input("\n是否继续？(y/n): ").strip().lower() != 'y':
                print("退出程序")
                break

    def search_and_download(self):
        """搜索并下载"""
        keyword = input("请输入搜索关键词: ").strip()
        if not keyword:
            print("搜索关键词不能为空")
            return

        max_results = input("请输入最大返回结果数 (默认10): ").strip()
        max_results = int(max_results) if max_results.isdigit() else 10

        standards = self.search_standards(keyword, max_results)
        if not standards:
            print("未找到相关标准")
            return

        print("\n请选择要下载的标准:")
        selection = input("输入序号 (多个用逗号分隔，或输入'all'下载全部): ").strip()

        selected_standards = []
        if selection.lower() == 'all':
            selected_standards = standards
            print(f"将下载全部 {len(standards)} 个标准")
        else:
            try:
                for idx in [int(x.strip()) for x in selection.split(',')]:
                    if 1 <= idx <= len(standards):
                        selected_standards.append(standards[idx - 1])
                    else:
                        print(f"无效序号: {idx}")
            except ValueError:
                print("输入格式错误")
                return

        if not selected_standards:
            print("没有选择任何标准")
            return

        max_workers = input("并发下载数 (默认1，建议1-2): ").strip()
        max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1
        self.batch_download(selected_standards, max_workers)

    def direct_download(self):
        """直接输入标准号下载"""
        input_text = input("请输入标准号 (多个用逗号分隔): ").strip()
        if not input_text:
            print("输入不能为空")
            return

        standards_input = [std.strip() for std in input_text.split(',')]
        all_standards = []
        for std_input in standards_input:
            if std_input:
                print(f"\n搜索: {std_input}")
                standards = self.search_standards(std_input, max_results=1)
                if standards:
                    all_standards.append(standards[0])
                else:
                    print(f"未找到: {std_input}")

        if not all_standards:
            print("没有找到任何标准")
            return

        max_workers = input("并发下载数 (默认1，建议1-2): ").strip()
        max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1
        self.batch_download(all_standards, max_workers)

    def txt_file_download(self):
        """从txt文件下载"""
        filepath = input("请输入txt文件路径 (直接回车使用默认文件standards.txt): ").strip()
        if not filepath:
            filepath = "standards.txt"
            print(f"使用默认文件: {filepath}")

        if not os.path.exists(filepath):
            print(f"文件不存在: {filepath}")
            if input("是否创建新文件？(y/n): ").strip().lower() == 'y':
                self.create_example_txt_file(filepath)
                print(f"请编辑 {filepath} 文件，添加标准号后再运行此功能")
            else:
                print("操作取消")
            return

        max_workers = input("并发下载数 (默认1，建议1-2): ").strip()
        max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1
        self.search_and_download_from_txt(filepath, max_workers)

    def search_single_standard(self):
        """搜索单个标准详细信息"""
        standard_number = input("请输入标准号: ").strip()
        if not standard_number:
            print("标准号不能为空")
            return
        self.search_detailed_info(standard_number)


def main():
    downloader = GBBatchSearchDownloader()
    downloader.run_interactive_mode()


if __name__ == "__main__":
    main()
