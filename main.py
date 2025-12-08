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
        self.base_download_url = "http://c.gb688.cn/bzgk/gb/"
        self.ocr = ddddocr.DdddOcr()
        
        # 通用Headers
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }

    def get_jsessionid(self):
        """获取JSESSIONID"""
        init_url = "http://c.gb688.cn/bzgk/gb/index"
        response = self.session.get(init_url, headers=self.headers)
        
        cookies = response.headers.get("Set-Cookie", "")
        jsessionid_match = re.search(r"JSESSIONID=([^;]+)", cookies)
        
        if jsessionid_match:
            self.jsessionid = jsessionid_match.group(1)
            self.headers["Cookie"] = f"JSESSIONID={self.jsessionid};"
            return True
        else:
            # 备用方法
            gc_url = f"{self.base_download_url}gc"
            response = self.session.get(gc_url, headers=self.headers)
            
            cookies = response.headers.get("Set-Cookie", "")
            jsessionid_match = re.search(r"JSESSIONID=([^;]+)", cookies)
            
            if jsessionid_match:
                self.jsessionid = jsessionid_match.group(1)
                self.headers["Cookie"] = f"JSESSIONID={self.jsessionid};"
                return True
            
            return False

    def get_verify_code(self):
        """获取验证码并识别"""
        timestamp = int(time.time() * 1000)
        url = f"{self.base_download_url}gc?t={timestamp}"
        
        # 设置正确的Referer
        headers = self.headers.copy()
        headers["Referer"] = f"http://c.gb688.cn/bzgk/gb/showGb?type=download&hcno={self.hcno}"
        
        response = self.session.get(url, headers=headers)
        
        if response.status_code == 200:
            verify_code = self.ocr.classification(response.content)
            verify_code = ''.join(c for c in verify_code if c.isalnum()).upper()
            
            if len(verify_code) != 4:
                if len(verify_code) < 4:
                    verify_code = verify_code.ljust(4, 'X')
                else:
                    verify_code = verify_code[:4]
            
            return verify_code
        else:
            return None

    def verify_code(self, code):
        """提交验证码"""
        url = f"{self.base_download_url}verifyCode"
        form_data = {"verifyCode": code}
        
        headers = {
            "Host": "c.gb688.cn",
            "Content-Length": str(len(f"verifyCode={code}")),
            "Accept": "text/plain, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "http://c.gb688.cn",
            "Referer": f"http://c.gb688.cn/bzgk/gb/showGb?type=download&hcno={self.hcno}",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": f"JSESSIONID={self.jsessionid}" if self.jsessionid else "",
            "Connection": "close"
        }
        
        try:
            response = self.session.post(url, data=form_data, headers=headers, timeout=10)
            
            if response.status_code == 200:
                response_text = response.text.strip()
                if "success" in response_text.lower():
                    return True
            return False
                
        except Exception:
            return False

    def download_pdf(self):
        """下载PDF文件"""
        # 第一步：获取JSESSIONID
        if not self.get_jsessionid():
            print(f"{self.standard_number}: 获取JSESSIONID失败")
            return None
        
        # 第二步：验证验证码
        max_attempts = 5
        verify_code = None
        
        for attempt in range(max_attempts):
            verify_code = self.get_verify_code()
            if not verify_code:
                print(f"{self.standard_number}: 获取验证码失败，尝试 {attempt+1}/{max_attempts}")
                time.sleep(1)
                continue
                
            if self.verify_code(verify_code):
                break
                
            print(f"{self.standard_number}: 验证码验证失败，尝试 {attempt+1}/{max_attempts}")
            
            if attempt == max_attempts - 1:
                print(f"{self.standard_number}: 验证码验证失败次数过多")
                return None
            
            time.sleep(2)
        
        # 第三步：下载PDF
        pdf_url = f"http://c.gb688.cn/bzgk/gb/viewGb?hcno={self.hcno}"
        
        headers = {
            "Host": "c.gb688.cn",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Referer": f"http://c.gb688.cn/bzgk/gb/showGb?type=download&hcno={self.hcno}",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": f"JSESSIONID={self.jsessionid}" if self.jsessionid else "",
            "Connection": "close"
        }
        
        try:
            response = self.session.get(pdf_url, headers=headers, stream=True, timeout=60)
            
            if response.status_code != 200:
                print(f"{self.standard_number}: 下载失败，状态码: {response.status_code}")
                return None
            
            # 生成文件名
            safe_number = self.standard_number.replace("/", "_").replace(" ", "_")
            filename = f"{safe_number}.pdf"
            
            # 检查文件是否已存在
            counter = 1
            while os.path.exists(filename):
                filename = f"{safe_number}_{counter}.pdf"
                counter += 1
            
            # 获取文件大小
            content_length = response.headers.get('Content-Length', '0')
            total_size = int(content_length) if content_length.isdigit() else 0
            
            # 下载文件
            downloaded_size = 0
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            percent = (downloaded_size / total_size) * 100
                            print(f"\r{self.standard_number}: 下载进度 {percent:.1f}% ({downloaded_size}/{total_size})", end='')
            
            # 验证文件
            if downloaded_size > 100:  # 文件大小要大于100字节
                with open(filename, 'rb') as f:
                    header = f.read(5)
                    if header == b'%PDF-':
                        print(f"\r{self.standard_number}: ✅ 下载成功 ({downloaded_size} 字节)")
                        return {
                            "filename": filename,
                            "standard_number": self.standard_number,
                            "standard_name": self.standard_name,
                            "size": downloaded_size
                        }
                    else:
                        # 尝试检查文件内容
                        f.seek(0)
                        content_start = f.read(100).decode('utf-8', errors='ignore')
                        if '<html' in content_start.lower():
                            print(f"\r{self.standard_number}: ❌ 下载到HTML页面，可能是验证失败")
                            os.remove(filename)
                            return None
            
            print(f"\r{self.standard_number}: ❌ 文件无效或太小 ({downloaded_size} 字节)")
            if os.path.exists(filename):
                os.remove(filename)
            return None
                
        except Exception as e:
            print(f"\r{self.standard_number}: ❌ 下载异常: {e}")
            return None


class GBBatchSearchDownloader:
    """GB标准批量搜索下载器"""
    def __init__(self):
        self.base_search_url = "https://openstd.samr.gov.cn/bzgk/gb/"
        self.session = requests.Session()
        self.search_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
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
            else:
                print(f"搜索失败，状态码: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"搜索异常: {e}")
            return []

    def parse_search_results(self, html_content, max_results):
        """解析搜索结果 - 返回详细信息"""
        soup = BeautifulSoup(html_content, "html.parser")
        standards = []
        
        # 查找搜索结果表格
        table = soup.find("table", class_="table")
        if not table:
            print("未找到搜索结果")
            return standards
        
        # 查找所有行
        rows = table.find_all("tr")
        if len(rows) <= 1:
            print("未找到搜索结果")
            return standards
        
        # 提取数据
        for i, row in enumerate(rows[1:max_results+1] if max_results else rows[1:]):
            try:
                cells = row.find_all("td")
                if len(cells) < 8:
                    continue
                
                # 提取详细信息
                standard_info = {}
                
                # 提取标准号
                standard_number_link = cells[1].find("a", onclick=re.compile(r"showInfo\('([A-F0-9]+)'\)"))
                if standard_number_link:
                    onclick_attr = standard_number_link.get("onclick", "")
                    hcno_match = re.search(r"showInfo\('([A-F0-9]+)'\)", onclick_attr)
                    if hcno_match:
                        standard_info["hcno"] = hcno_match.group(1)
                
                standard_info["standard_number"] = cells[1].get_text(strip=True)
                
                # 提取是否采标
                standard_info["adopted_standard"] = cells[2].get_text(strip=True)
                
                # 提取标准名称
                standard_name_cell = cells[3]
                standard_name_link = standard_name_cell.find("a", onclick=re.compile(r"showInfo\('([A-F0-9]+)'\)"))
                if standard_name_link:
                    standard_info["standard_name"] = standard_name_link.get_text(strip=True)
                else:
                    standard_info["standard_name"] = standard_name_cell.get_text(strip=True)
                
                # 提取类别
                standard_info["category"] = cells[4].get_text(strip=True)
                
                # 提取状态
                status_cell = cells[5]
                status_text = status_cell.get_text(strip=True)
                # 如果状态文本为空，尝试查找span标签
                if not status_text:
                    status_span = status_cell.find("span")
                    if status_span:
                        status_text = status_span.get_text(strip=True)
                standard_info["status"] = status_text
                
                # 提取发布日期
                standard_info["publish_date"] = cells[6].get_text(strip=True)
                
                # 提取实施日期
                standard_info["implement_date"] = cells[7].get_text(strip=True)
                
                standards.append(standard_info)
                print(f"  {i+1}. {standard_info['standard_number']} - {standard_info['standard_name'][:40]}...")
                
            except Exception as e:
                print(f"解析第{i+1}行时出错: {e}")
                continue
        
        print(f"找到 {len(standards)} 个标准")
        return standards

    def batch_download(self, standards_list, max_workers=1):
        """批量下载 - 修正版：每个标准独立验证"""
        if not standards_list:
            print("没有可下载的标准")
            return [], []
        
        print(f"\n开始批量下载 {len(standards_list)} 个标准...")
        print(f"使用 {max_workers} 个并发线程")
        print("=" * 60)
        
        downloaded_files = []
        failed_downloads = []
        
        # 使用线程池
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            for standard_info in standards_list:
                # 为每个标准创建独立的下载器
                downloader = StandardDownloader(
                    hcno=standard_info["hcno"],
                    standard_number=standard_info["standard_number"],
                    standard_name=standard_info["standard_name"]
                )
                
                # 提交下载任务
                future = executor.submit(downloader.download_pdf)
                futures.append(future)
                
                # 控制请求频率
                time.sleep(0.5)
            
            # 处理结果
            for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
                try:
                    result = future.result(timeout=300)
                    if result:
                        downloaded_files.append(result)
                    else:
                        # 获取对应的标准信息
                        std_info = standards_list[i-1]
                        failed_downloads.append({
                            "standard_number": std_info["standard_number"],
                            "standard_name": std_info["standard_name"],
                            "error": "下载失败"
                        })
                except concurrent.futures.TimeoutError:
                    std_info = standards_list[i-1]
                    failed_downloads.append({
                        "standard_number": std_info["standard_number"],
                        "standard_name": std_info["standard_name"],
                        "error": "任务超时"
                    })
                except Exception as e:
                    std_info = standards_list[i-1]
                    failed_downloads.append({
                        "standard_number": std_info["standard_number"],
                        "standard_name": std_info["standard_name"],
                        "error": str(e)
                    })
        
        # 输出结果
        print(f"\n{'='*60}")
        print(f"批量下载完成!")
        print(f"{'='*60}")
        
        if downloaded_files:
            print(f"\n✅ 成功下载: {len(downloaded_files)} 个")
            total_size = 0
            for item in downloaded_files:
                print(f"  • {item['standard_number']:20} -> {item['filename']}")
                total_size += item.get('size', 0)
            if total_size > 0:
                print(f"  总计大小: {total_size:,} 字节 ({total_size/1024/1024:.2f} MB)")
        
        if failed_downloads:
            print(f"\n❌ 失败: {len(failed_downloads)} 个")
            for item in failed_downloads:
                print(f"  • {item['standard_number']:20} -> {item.get('error', '未知错误')}")
        
        return downloaded_files, failed_downloads

    def read_standards_from_txt(self, filepath):
        """
        从txt文件读取标准号 - 修复版
        支持格式：
        - 每行一个标准号（可包含空格，如 "GB/T 28458-2020"）
        - 用逗号、分号分隔的多个标准号
        - 空行和以#开头的注释行会被忽略
        """
        if not os.path.exists(filepath):
            print(f"❌ 文件不存在: {filepath}")
            return []
        
        standards = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            print(f"读取文件: {filepath} ({len(lines)} 行)")
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                
                # 跳过空行和注释
                if not line or line.startswith('#'):
                    continue
                
                # 尝试按明确的分隔符分割
                if ',' in line:
                    # 逗号分割，然后清理每个部分
                    parts = line.split(',')
                    for part in parts:
                        part = part.strip()
                        if part and not part.startswith('#'):  # 跳过注释部分
                            standards.append(part)
                elif ';' in line:
                    # 分号分割
                    parts = line.split(';')
                    for part in parts:
                        part = part.strip()
                        if part and not part.startswith('#'):
                            standards.append(part)
                elif '\t' in line:
                    # 制表符分割
                    parts = line.split('\t')
                    for part in parts:
                        part = part.strip()
                        if part and not part.startswith('#'):
                            standards.append(part)
                else:
                    # 没有明确分隔符，将整行视为一个标准号
                    # 这是修复的关键：不再按空格分割
                    standards.append(line)
            
            # 去重
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
            print(f"❌ 读取文件失败: {e}")
            return []

    def search_and_download_from_txt(self, filepath, max_workers=1):
        """从txt文件读取标准号并批量下载"""
        # 1. 从文件读取标准号
        standard_numbers = self.read_standards_from_txt(filepath)
        if not standard_numbers:
            print("没有读取到有效的标准号")
            return
        
        print(f"\n将搜索并下载以下标准:")
        for i, std in enumerate(standard_numbers, 1):
            print(f"  {i}. {std}")
        
        # 2. 确认是否继续
        confirm = input(f"\n确认搜索并下载以上 {len(standard_numbers)} 个标准？(y/n): ").strip().lower()
        if confirm != 'y':
            print("操作取消")
            return
        
        # 3. 搜索每个标准号获取详细信息
        print(f"\n正在搜索标准信息...")
        all_standards = []
        not_found = []
        
        for i, std_number in enumerate(standard_numbers, 1):
            print(f"\n[{i}/{len(standard_numbers)}] 搜索: {std_number}")
            standards = self.search_standards(std_number, max_results=1)
            
            if standards:
                all_standards.append(standards[0])
                print(f"    找到: {standards[0]['standard_number']} - {standards[0]['standard_name'][:30]}...")
            else:
                not_found.append(std_number)
                print(f"    ❌ 未找到: {std_number}")
            
            # 避免请求过于频繁
            time.sleep(1)
        
        # 4. 显示搜索结果
        if not_found:
            print(f"\n❌ 以下标准未找到:")
            for std in not_found:
                print(f"  • {std}")
        
        if not all_standards:
            print("没有找到任何标准")
            return
        
        # 5. 下载找到的标准
        print(f"\n找到 {len(all_standards)} 个标准，开始下载...")
        
        # 设置并发数
        if max_workers <= 0:
            max_workers = input(f"并发下载数 (默认1，建议1-2): ").strip()
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

# 示例4：混合使用空格的标准号（会被正确识别为一个完整标准号）
GB/T 28458-2020
GB/T 25061-2020

# 注意：不要在标准号内部使用逗号或分号
# 错误示例：GB/T,28448-2019
# 正确示例：GB/T 28448-2019 或 GB/T 28448-2019, GB/T 22239-2019
"""
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(example_content)
            print(f"✅ 示例文件已创建: {filepath}")
            print(f"文件包含各种格式的示例，你可以参考修改")
            return True
        except Exception as e:
            print(f"❌ 创建示例文件失败: {e}")
            return False

    def batch_search_info_to_excel(self, txt_file=None, excel_file=None):
        """
        从txt文件批量搜索标准信息并导出为Excel
        
        Args:
            txt_file: 包含标准号的txt文件路径
            excel_file: 导出的Excel文件路径
        """
        # 1. 获取输入文件
        if not txt_file:
            txt_file = input("请输入包含标准号的txt文件路径 (直接回车使用默认文件standards.txt): ").strip()
            if not txt_file:
                txt_file = "standards.txt"
        
        if not os.path.exists(txt_file):
            print(f"❌ 文件不存在: {txt_file}")
            create_new = input("是否创建示例文件？(y/n): ").strip().lower()
            if create_new == 'y':
                self.create_example_txt_file(txt_file)
                print(f"请编辑 {txt_file} 文件，添加标准号后再运行此功能")
                return
            else:
                print("操作取消")
                return
        
        # 2. 读取标准号
        standard_numbers = self.read_standards_from_txt(txt_file)
        if not standard_numbers:
            print("没有读取到有效的标准号")
            return
        
        # 3. 获取输出文件路径
        if not excel_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_excel = f"standards_info_{timestamp}.xlsx"
            excel_file = input(f"请输入导出的Excel文件路径 (直接回车使用 {default_excel}): ").strip()
            if not excel_file:
                excel_file = default_excel
        
        # 4. 确认操作
        print(f"\n将要搜索以下 {len(standard_numbers)} 个标准的信息:")
        for i, std in enumerate(standard_numbers[:10], 1):
            print(f"  {i}. {std}")
        if len(standard_numbers) > 10:
            print(f"  ... 等 {len(standard_numbers)} 个标准")
        
        confirm = input(f"\n确认搜索并导出到Excel？(y/n): ").strip().lower()
        if confirm != 'y':
            print("操作取消")
            return
        
        # 5. 批量搜索标准信息
        print(f"\n开始批量搜索标准信息...")
        print("=" * 80)
        
        all_standards_info = []
        not_found_standards = []
        
        total = len(standard_numbers)
        for i, std_number in enumerate(standard_numbers, 1):
            print(f"\n[{i}/{total}] 搜索: {std_number}")
            
            try:
                # 搜索标准
                standards = self.search_standards(std_number, max_results=1)
                
                if standards:
                    std_info = standards[0]
                    
                    # 添加额外信息
                    std_info["search_keyword"] = std_number
                    std_info["found"] = "是"
                    
                    all_standards_info.append(std_info)
                    print(f"    ✅ 找到: {std_info['standard_number']} - {std_info['standard_name'][:30]}...")
                else:
                    not_found_standards.append({
                        "search_keyword": std_number,
                        "standard_number": std_number,
                        "standard_name": "",
                        "adopted_standard": "",
                        "category": "",
                        "status": "",
                        "publish_date": "",
                        "implement_date": "",
                        "found": "否",
                        "error": "未找到"
                    })
                    print(f"    ❌ 未找到: {std_number}")
                
            except Exception as e:
                not_found_standards.append({
                    "search_keyword": std_number,
                    "standard_number": std_number,
                    "standard_name": "",
                    "adopted_standard": "",
                    "category": "",
                    "status": "",
                    "publish_date": "",
                    "implement_date": "",
                    "found": "否",
                    "error": str(e)[:50]
                })
                print(f"    ❌ 搜索出错: {std_number} - {e}")
            
            # 控制请求频率，避免被封
            time.sleep(1)
        
        # 6. 合并结果
        all_results = all_standards_info + not_found_standards
        
        # 7. 导出到Excel
        try:
            # 转换为DataFrame
            df = pd.DataFrame(all_results)
            
            # 重新排列列顺序
            column_order = [
                "search_keyword", "found", "standard_number", "standard_name", 
                "adopted_standard", "category", "status", 
                "publish_date", "implement_date", "hcno", "error"
            ]
            
            # 只保留存在的列
            existing_columns = [col for col in column_order if col in df.columns]
            df = df[existing_columns]
            
            # 导出到Excel
            with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='标准信息')
                
                # 设置列宽
                worksheet = writer.sheets['标准信息']
                for i, col in enumerate(df.columns, 1):
                    if col in ["standard_name"]:
                        worksheet.column_dimensions[chr(64+i)].width = 40
                    elif col in ["standard_number"]:
                        worksheet.column_dimensions[chr(64+i)].width = 20
                    else:
                        worksheet.column_dimensions[chr(64+i)].width = 15
            
            # 8. 输出统计信息
            found_count = len(all_standards_info)
            not_found_count = len(not_found_standards)
            total_count = len(all_results)
            
            print(f"\n{'='*80}")
            print("搜索结果统计:")
            print(f"{'='*80}")
            print(f"✅ 找到: {found_count} 个标准")
            print(f"❌ 未找到: {not_found_count} 个标准")
            print(f"📊 总计: {total_count} 个标准")
            print(f"💾 Excel文件已保存: {excel_file}")
            
            # 显示文件大小
            if os.path.exists(excel_file):
                file_size = os.path.getsize(excel_file)
                print(f"📁 文件大小: {file_size:,} 字节 ({file_size/1024:.1f} KB)")
            
            # 打开Excel文件的选项
            open_excel = input(f"\n是否打开Excel文件？(y/n): ").strip().lower()
            if open_excel == 'y':
                try:
                    os.startfile(excel_file)  # Windows
                    print("正在打开Excel文件...")
                except:
                    try:
                        import subprocess
                        subprocess.run(['open', excel_file])  # macOS
                    except:
                        try:
                            subprocess.run(['xdg-open', excel_file])  # Linux
                        except:
                            print("无法自动打开文件，请手动打开。")
            
            return excel_file
            
        except Exception as e:
            print(f"❌ 导出Excel失败: {e}")
            print("尝试导出为CSV格式...")
            
            # 尝试导出为CSV
            try:
                csv_file = excel_file.replace('.xlsx', '.csv')
                df = pd.DataFrame(all_results)
                df.to_csv(csv_file, index=False, encoding='utf-8-sig')
                print(f"✅ CSV文件已保存: {csv_file}")
                return csv_file
            except Exception as e2:
                print(f"❌ 导出CSV也失败: {e2}")
                # 打印部分结果
                print("\n前10条搜索结果:")
                for i, result in enumerate(all_results[:10], 1):
                    print(f"{i}. {result.get('standard_number', 'N/A')} - {result.get('standard_name', 'N/A')}")
                return None

    def search_detailed_info(self, standard_number):
        """
        搜索单个标准的详细信息
        
        Args:
            standard_number: 标准号
            
        Returns:
            dict: 标准详细信息
        """
        print(f"搜索标准详情: {standard_number}")
        
        try:
            standards = self.search_standards(standard_number, max_results=1)
            
            if standards:
                std_info = standards[0]
                
                # 格式化输出
                print("\n" + "="*60)
                print(f"标准号: {std_info.get('standard_number', 'N/A')}")
                print(f"标准名称: {std_info.get('standard_name', 'N/A')}")
                print(f"是否采标: {std_info.get('adopted_standard', 'N/A')}")
                print(f"类别: {std_info.get('category', 'N/A')}")
                print(f"状态: {std_info.get('status', 'N/A')}")
                print(f"发布日期: {std_info.get('publish_date', 'N/A')}")
                print(f"实施日期: {std_info.get('implement_date', 'N/A')}")
                print(f"HCNO: {std_info.get('hcno', 'N/A')}")
                print("="*60)
                
                return std_info
            else:
                print(f"未找到标准: {standard_number}")
                return None
                
        except Exception as e:
            print(f"搜索失败: {e}")
            return None

    def run_interactive_mode(self):
        """交互式运行模式"""
        print("GB标准批量搜索下载工具 by liuliuliu")
        print("="*60)
        
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
            elif choice == "7":
                print("退出程序")
                break
            else:
                print("无效选择")
            
            # 询问是否继续
            if choice not in ['6']:  # 创建示例文件后不需要询问
                continue_choice = input("\n是否继续？(y/n): ").strip().lower()
                if continue_choice != 'y':
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
        
        # 搜索
        standards = self.search_standards(keyword, max_results)
        
        if not standards:
            print("未找到相关标准")
            return
        
        # 选择要下载的标准
        print(f"\n请选择要下载的标准:")
        selection = input("输入序号 (多个用逗号分隔，或输入'all'下载全部): ").strip()
        
        selected_standards = []
        if selection.lower() == 'all':
            selected_standards = standards
            print(f"将下载全部 {len(standards)} 个标准")
        else:
            try:
                indices = [int(idx.strip()) for idx in selection.split(',')]
                for idx in indices:
                    if 1 <= idx <= len(standards):
                        selected_standards.append(standards[idx-1])
                    else:
                        print(f"无效序号: {idx}")
            except ValueError:
                print("输入格式错误")
                return
        
        if not selected_standards:
            print("没有选择任何标准")
            return
        
        # 设置并发数
        max_workers = input(f"并发下载数 (默认1，建议1-2): ").strip()
        max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1
        
        # 下载
        print(f"\n开始下载 {len(selected_standards)} 个标准...")
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
        
        # 设置并发数
        max_workers = input(f"并发下载数 (默认1，建议1-2): ").strip()
        max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1
        
        # 下载
        self.batch_download(all_standards, max_workers)

    def txt_file_download(self):
        """从txt文件下载"""
        filepath = input("请输入txt文件路径 (直接回车使用默认文件standards.txt): ").strip()
        if not filepath:
            filepath = "standards.txt"
            print(f"使用默认文件: {filepath}")
        
        # 检查文件是否存在
        if not os.path.exists(filepath):
            print(f"文件不存在: {filepath}")
            create_new = input("是否创建新文件？(y/n): ").strip().lower()
            if create_new == 'y':
                self.create_example_txt_file(filepath)
                print(f"请编辑 {filepath} 文件，添加标准号后再运行此功能")
                return
            else:
                print("操作取消")
                return
        
        # 设置并发数
        max_workers = input(f"并发下载数 (默认1，建议1-2): ").strip()
        max_workers = int(max_workers) if max_workers.isdigit() and int(max_workers) > 0 else 1
        
        # 从文件下载
        self.search_and_download_from_txt(filepath, max_workers)

    def search_single_standard(self):
        """搜索单个标准详细信息"""
        standard_number = input("请输入标准号: ").strip()
        if not standard_number:
            print("标准号不能为空")
            return
        
        self.search_detailed_info(standard_number)


def main():
    """主函数"""
    downloader = GBBatchSearchDownloader()
    downloader.run_interactive_mode()


if __name__ == "__main__":
    main()