# -*- coding: utf-8 -*-
"""批量下载/压测脚本（可选工具）
用法: python bench_batch.py <并发数> <输出目录> 标准号1 标准号2 ...
"""
import os, sys, time
import concurrent.futures
import main as g


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    outdir = sys.argv[2] if len(sys.argv) > 2 else "bench_out"
    keywords = sys.argv[3:]

    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), outdir)
    os.makedirs(outdir, exist_ok=True)
    os.chdir(outdir)

    searcher = g.GBBatchSearchDownloader()
    print("=" * 78)
    print(f"阶段1 查询 {len(keywords)} 个标准号")
    print("=" * 78)

    t0 = time.time()
    found, not_found = [], []
    for kw in keywords:
        res = searcher.search_standards(kw, max_results=1)
        if res:
            found.append(res[0])
        else:
            not_found.append(kw)
        time.sleep(0.8)
    t_search = time.time() - t0

    print(f"\n查询完成: 命中 {len(found)} / 未命中 {len(not_found)}，耗时 {t_search:.1f}s")
    for kw in not_found:
        print(f"  未命中: {kw}")

    if not found:
        return

    print("\n" + "=" * 78)
    print(f"阶段2 下载（并发 {workers}）")
    print("=" * 78)

    results = []
    t1 = time.time()

    def timed_job(info):
        d = g.StandardDownloader(info["hcno"], info["standard_number"],
                                 info.get("standard_name", ""))
        t = time.time()
        r = d.download_pdf()
        return r, time.time() - t, d.fail_reason

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {}
        for info in found:
            f = ex.submit(timed_job, info)
            future_map[f] = info
            time.sleep(0.4)

        for f in concurrent.futures.as_completed(future_map):
            info = future_map[f]
            try:
                r, cost, reason = f.result(timeout=600)
            except Exception as e:
                r, cost, reason = None, 0, str(e)
                print(f"  {info['standard_number']} 异常: {e}")
            if r:
                mbs = (r["size"] / 1024 / 1024) / cost if cost else 0
                results.append((info["standard_number"], r["size"], cost, mbs))
                print(f"\n  [完成] {info['standard_number']:22} {r['size']/1024/1024:6.2f} MB  用时 {cost:5.1f}s  {mbs:5.2f} MB/s")
            else:
                results.append((info["standard_number"], 0, cost, 0))
                print(f"\n  [失败] {info['standard_number']:22} 用时 {cost:5.1f}s  <- {reason}")
    t_dl = time.time() - t1

    ok = [r for r in results if r[1] > 0]
    total_bytes = sum(r[1] for r in ok)
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"查询耗时      : {t_search:.1f}s")
    print(f"下载总耗时    : {t_dl:.1f}s")
    print(f"全局总耗时    : {t_search + t_dl:.1f}s")
    print(f"成功/总数     : {len(ok)}/{len(results)}")
    print(f"总下载量      : {total_bytes/1024/1024:.2f} MB")
    if t_dl > 0:
        print(f"端到端吞吐    : {total_bytes/1024/1024/t_dl:.2f} MB/s")
    if ok:
        avg = sum(r[2] for r in ok) / len(ok)
        print(f"单文件平均    : {avg:.1f}s")
        print(f"单文件最快/最慢: {min(r[2] for r in ok):.1f}s / {max(r[2] for r in ok):.1f}s")
    print("\n明细:")
    print(f"  {'标准号':24} {'大小(MB)':>9} {'耗时(s)':>8} {'MB/s':>7}")
    for num, size, cost, mbs in results:
        print(f"  {num:24} {size/1024/1024:9.2f} {cost:8.1f} {mbs:7.2f}")


if __name__ == "__main__":
    main()
