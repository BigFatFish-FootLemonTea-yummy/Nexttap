# 前端构建镜像：Vue 3 → 静态文件
#
# 两阶段：node 里 build，nginx 里只放产物。
# 最终镜像不含 node_modules 和源码，体积从 ~400MB 降到 ~50MB。

FROM node:22-alpine AS build

WORKDIR /app

# 先只拷依赖清单，利用 Docker 层缓存：
# 改源码不会触发重新 npm install
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


FROM nginx:1.27-alpine

# 同域反代：/ 走静态文件，/api 和 /health 转发给 api 容器
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
