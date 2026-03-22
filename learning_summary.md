# Hetzner Cloud VPS 部署学习总结

# 学习总结：Hetzner Cloud VPS 部署与配置

## 学到的关键步骤和最佳实践

1.  **Hetzner Cloud VPS 基础配置 (Ubuntu 22.04)**
    *   创建项目并选择 Ubuntu 22.04 镜像、最小规格（如 CX11）。
    *   配置 SSH 密钥对进行安全登录，禁用密码登录。
    *   首次登录后立即执行系统更新 (`sudo apt update && sudo apt upgrade -y`)。
    *   创建具有 sudo 权限的非 root 用户。

2.  **Docker 与 Docker Compose 部署**
    *   使用官方脚本安装 Docker Engine (`curl -fsSL https://get.docker.com -o get-docker.sh`)。
    *   将当前用户加入 `docker` 组以非 root 运行 (`sudo usermod -aG docker $USER`)。
    *   通过 GitHub 发布页面下载 Docker Compose 二进制文件并赋予执行权限。
    *   最佳实践：使用 `docker-compose.yml` 文件定义和管理多容器应用。

3.  **Caddy 反向代理配置 HTTPS**
    *   安装 Caddy（通过包管理器或下载二进制）。
    *   核心配置：在 `Caddyfile` 中使用 `example.com` 域名，Caddy 会自动获取并管理 Let's Encrypt SSL 证书。
    *   反向代理基本语法：`reverse_proxy <local_service_ip:port>`。
    *   最佳实践：同时开放 80 和 443 端口，Caddy 自动处理 HTTP 到 HTTPS 的重定向。

4.  **Hetzner Firewall 规则配置**
    *   **云防火墙**：在 Hetzner Cloud Console 的网络标签下配置，独立于服务器系统防火墙。
    *   **关键规则**：允许 SSH (22/tcp)、HTTP (80/tcp)、HTTPS (443/tcp) 入站流量。
    *   默认拒绝所有入站，明确允许所需端口。
    *   可配置源 IP 限制（如仅允许自己 IP 访问 SSH）。

## 发现的常见坑点和注意事项

*   **防火墙双重管理**：注意区分 **Hetzner 云防火墙**（网络层面）和 **服务器内部防火墙**（如 UFW/iptables）。两者都需正确配置，否则可能导致端口无法访问。常见错误是只配置了一个。
*   **SSH 访问锁定**：在配置防火墙或禁用密码登录时，错误规则可能导致自己无法 SSH 连接服务器。务必在生效前测试规则，或保持一个活动会话。
*   **Caddy 自动 HTTPS**：需要域名 DNS 已正确解析到服务器 IP，且 80/443 端口可从公网访问（防火墙已放行），否则证书申请会失败。
*   **Docker 权限**：安装 Docker 后需注销并重新登录，或执行 `newgrp docker`，用户组更改才能生效，否则会遇到权限错误。
*   **端口冲突**：确保要开放的端口（如 80, 443）没有被系统上其他服务占用。

## 不确定需要验证的地方

1.  **Hetzner 云防火墙与系统 UFW 的优先级与交互**：当两者同时启用并设置规则时，具体生效顺序和最终效果如何？是否需要同时配置，还是建议只使用其中一种？
2.  **Caddy 在 Docker 中的最佳部署模式**：是直接在宿主机安装运行，还是作为 Docker 容器运行？如果容器化，如何最优雅地管理 `Caddyfile` 和证书数据的持久化存储？
3.  **Docker Compose 网络配置**：在 Compose 中，如何为应用容器（如 Web 应用）和 Caddy 容器配置网络，使得 Caddy 能够正确反向代理到应用容器，且不影响外部访问？
4.  **具体的性能影响**：在最小规格（如 1 vCPU, 2GB RAM）的 VPS 上，同时运行 Docker、多个应用容器和 Caddy，实际性能表现和资源占用率如何？是否需要调整优化？

---
*总结于学习任务执行后，需在实际操作中验证上述不确定点。*